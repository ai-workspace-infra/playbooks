#!/usr/bin/env python3
"""Bounded runtime-only K3s/Flux identity evidence. Never output raw resources."""
import argparse
import datetime
import json
import platform
import re
import socket
import subprocess
import time


def ready(obj):
    status = obj.get("status", {})
    generation = obj["metadata"]["generation"]
    return (status.get("observedGeneration") == generation and
            any(c.get("type") == "Ready" and c.get("status") == "True" and
                c.get("observedGeneration", generation) == generation
                for c in status.get("conditions", [])))


def evaluate(nodes, cluster, source, root, expected_node, commit):
    items = nodes.get("items", [])
    if len(items) != 1 or items[0]["metadata"]["name"] != expected_node:
        raise ValueError("expected exactly the selected node")
    node = items[0]
    if not any(c.get("type") == "Ready" and c.get("status") == "True" for c in node.get("status", {}).get("conditions", [])):
        raise ValueError("node is not Ready")
    if "node-role.kubernetes.io/control-plane" not in node["metadata"].get("labels", {}):
        raise ValueError("selected node is not control-plane")
    if commit and source.get("spec", {}).get("ref", {}).get("commit") != commit:
        raise ValueError("source spec is not pinned to expected commit")
    if not ready(source) or not ready(root):
        raise ValueError("Flux generation is not Ready")
    revision = source.get("status", {}).get("artifact", {}).get("revision", "")
    actual = revision.rsplit("sha1:", 1)[-1]
    if not re.fullmatch(r"[0-9a-f]{40}", actual) or (commit and actual != commit):
        raise ValueError("source revision does not match commit")
    if root.get("status", {}).get("lastAppliedRevision") != revision:
        raise ValueError("root has not applied the source revision")
    for obj in (node, cluster, source, root):
        if not obj["metadata"].get("uid"):
            raise ValueError("runtime resource UID missing")
    return {"schema_version": 1, "result": "PASS", "node": expected_node,
            "node_uid": node["metadata"]["uid"], "cluster_uid": cluster["metadata"]["uid"],
            "node_ready": True, "source_ready": True, "root_ready": True,
            "source_revision": revision, "applied_revision": root["status"]["lastAppliedRevision"],
            "node_info": {k: node["status"].get("nodeInfo", {}).get(k) for k in ("architecture", "operatingSystem", "osImage", "kubeletVersion", "containerRuntimeVersion")}}


def get(*args):
    result = subprocess.run(["kubectl", *args, "-o", "json"], capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise ValueError("runtime Kubernetes observation failed")
    return json.loads(result.stdout)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--node", required=True)
    p.add_argument("--namespace", required=True)
    p.add_argument("--source", required=True)
    p.add_argument("--commit", default="")
    p.add_argument("--timeout", type=int, default=600)
    a = p.parse_args()
    if a.commit and not re.fullmatch(r"[0-9a-f]{40}", a.commit):
        raise SystemExit("invalid expected commit")
    if not 1 <= a.timeout <= 1200:
        raise SystemExit("invalid observation timeout")
    deadline = time.monotonic() + a.timeout
    while True:
        try:
            receipt = evaluate(get("get", "nodes"), get("get", "namespace", "kube-system"),
                               get("-n", a.namespace, "get", "gitrepository", a.source),
                               get("-n", a.namespace, "get", "kustomization", "platform-root"), a.node, a.commit)
            if socket.gethostname() != a.node:
                raise ValueError("OS hostname does not match selected node")
            receipt.update(hostname=socket.gethostname(), architecture=platform.machine(),
                           observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
            print(json.dumps(receipt, sort_keys=True))
            return
        except (ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
            if time.monotonic() >= deadline:
                raise SystemExit("bootstrap identity/Ready/revision verification timed out") from None
            time.sleep(min(5, max(0, deadline-time.monotonic())))


if __name__ == "__main__":
    main()
