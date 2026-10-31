#!/usr/bin/env python3
"""Read real candidate state; paused declarations do not claim a business release."""
import argparse
import datetime
import json
import subprocess
from pathlib import Path
import yaml

KINDS = {'GitRepository', 'Kustomization', 'HelmRelease'}
RESOURCE = 'gitrepositories.source.toolkit.fluxcd.io,kustomizations.kustomize.toolkit.fluxcd.io,helmreleases.helm.toolkit.fluxcd.io'


def identity(obj):
    return (obj['kind'], obj['metadata'].get('namespace', 'default'), obj['metadata']['name'])


def verify(expected, actual, node, version):
    desired = {identity(o): o for o in expected if o and o.get('kind') in KINDS}
    observed = {identity(o): o for o in actual}
    if desired.keys() != observed.keys():
        raise ValueError('Live Flux object identities differ from the reviewed bundle')
    for key, want in desired.items():
        have = observed[key]
        if have.get('spec', {}).get('suspend') is not True:
            raise ValueError('A live release object is active')
        # API defaulting adds fields; every declared field must still match.
        def subset(a, b):
            return all(k in b and subset(v, b[k]) for k, v in a.items()) if isinstance(a, dict) else a == b
        if not subset(want['spec'], have.get('spec', {})):
            raise ValueError('Live release spec differs from declaration')
    if node['metadata']['name'] != node.get('expected_name'):
        raise ValueError('Node name mismatch')
    status = node['status']
    if status['nodeInfo']['kubeletVersion'] != version or status['nodeInfo']['architecture'] != 'amd64':
        raise ValueError('Node version or architecture mismatch')
    if not any(c['type'] == 'Ready' and c['status'] == 'True' for c in status['conditions']):
        raise ValueError('Node is not Ready')
    return {kind: sum(key[0] == kind for key in desired) for kind in sorted(KINDS)}


def run(*argv):
    return subprocess.check_output(argv, text=True).strip()


def main():
    p = argparse.ArgumentParser()
    for key in ('bundle', 'node', 'version', 'commit', 'volume-uuid'):
        p.add_argument('--' + key, required=True)
    a = p.parse_args()
    expected = list(yaml.safe_load_all(Path(a.bundle).read_text()))
    actual = json.loads(run('kubectl', 'get', RESOURCE, '-A', '-o', 'json'))['items']
    nodes = json.loads(run('kubectl', 'get', 'nodes', '-o', 'json'))['items']
    if len(nodes) != 1:
        raise ValueError('Expected one isolated candidate node')
    node = nodes[0]
    node['expected_name'] = a.node
    counts = verify(expected, actual, node, a.version)
    mount = run('findmnt', '-nro', 'TARGET,UUID,FSTYPE', '--mountpoint', '/data').split()
    if mount != ['/data', a.volume_uuid, 'ext4']:
        raise ValueError('Independent data volume UUID differs')
    ns = json.loads(run('kubectl', 'get', 'namespace', 'kube-system', '-o', 'json'))
    print(json.dumps({'result': 'PAUSED_BOOTSTRAP_VERIFIED', 'gitops_sha': a.commit,
                      'node': a.node, 'node_uid': node['metadata']['uid'],
                      'cluster_uid': ns['metadata']['uid'], 'version': a.version,
                      'volume_uuid': a.volume_uuid, 'counts': counts,
                      'observed_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}))


if __name__ == '__main__':
    main()
