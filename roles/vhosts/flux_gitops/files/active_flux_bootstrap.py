#!/usr/bin/env python3
"""Apply only reviewed source/root; Flux owns all child and service resources."""
import argparse
import datetime
import json
from pathlib import Path
import socket
import subprocess
import time
import yaml


def run(*args, **kwargs):
    return subprocess.check_output(args, text=True, timeout=45, **kwargs).strip()


def ready(obj):
    status = obj.get('status', {})
    generation = obj['metadata']['generation']
    return status.get('observedGeneration') == generation and any(
        c.get('type') == 'Ready' and c.get('status') == 'True'
        and c.get('observedGeneration', generation) == generation
        for c in status.get('conditions', []))


def entrypoints(docs, namespace, source, root):
    keys = {('GitRepository', namespace, source), ('Kustomization', namespace, root)}
    selected = [d for d in docs if (d['kind'], d['metadata'].get('namespace'), d['metadata']['name']) in keys]
    if len(selected) != 2 or any(d['spec'].get('suspend', False) for d in selected):
        raise ValueError('two active source/root entrypoints required')
    return selected


def main():
    p = argparse.ArgumentParser()
    for key in ('bundle', 'root', 'source', 'namespace', 'node', 'version', 'commit', 'volume-uuid'):
        p.add_argument('--' + key, required=True)
    a = p.parse_args()
    docs = list(yaml.safe_load_all(Path(a.bundle).read_text()))
    entries = entrypoints(docs, a.namespace, a.source, a.root)
    if socket.gethostname() != a.node:
        raise ValueError('OS hostname differs from the selected node')
    if run('findmnt', '-nro', 'TARGET,UUID,FSTYPE', '--mountpoint', '/data').split() != ['/data', a.volume_uuid, 'ext4']:
        raise ValueError('independent data volume differs')
    run('kubectl', 'apply', '-f', '-', input=yaml.safe_dump_all(entries))
    deadline = time.monotonic() + 600
    while True:
        nodes = json.loads(run('kubectl', 'get', 'nodes', '-o', 'json'))['items']
        if len(nodes) != 1 or nodes[0]['metadata']['name'] != a.node:
            raise ValueError('expected exactly the selected candidate node')
        node = nodes[0]
        if node['status']['nodeInfo']['kubeletVersion'] != a.version:
            raise ValueError('K3s version differs')
        objects = [json.loads(run('kubectl', 'get', d['kind'], d['metadata']['name'], '-n', a.namespace, '-o', 'json')) for d in entries]
        for want, have in zip(entries, objects):
            for field in ('ref', 'url', 'sourceRef', 'path'):
                if field in want['spec'] and have['spec'].get(field) != want['spec'][field]:
                    raise ValueError('live entrypoint differs from the reviewed graph')
        source = next(d for d in objects if d['kind'] == 'GitRepository')
        root = next(d for d in objects if d['kind'] == 'Kustomization')
        revision = source.get('status', {}).get('artifact', {}).get('revision', '')
        node_ready = any(c.get('type') == 'Ready' and c.get('status') == 'True' for c in node['status'].get('conditions', []))
        if (node_ready and all(ready(d) for d in objects) and revision.rsplit('sha1:', 1)[-1] == a.commit
                and root.get('status', {}).get('lastAppliedRevision') == revision):
            break
        if time.monotonic() >= deadline:
            raise ValueError('Flux handover readiness/revision timed out')
        time.sleep(5)
    cluster = json.loads(run('kubectl', 'get', 'namespace', 'kube-system', '-o', 'json'))
    print(json.dumps({'result': 'FLUX_HANDOVER_VERIFIED', 'gitops_sha': a.commit,
                      'node': a.node, 'version': a.version, 'node_uid': node['metadata']['uid'],
                      'cluster_uid': cluster['metadata']['uid'], 'volume_uuid': a.volume_uuid,
                      'source_revision': revision,
                      'observed_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}))


if __name__ == '__main__':
    main()
