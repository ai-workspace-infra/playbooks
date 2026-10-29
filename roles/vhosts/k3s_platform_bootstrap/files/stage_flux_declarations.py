#!/usr/bin/env python3
"""Build a fixed, initially paused Flux declaration graph; never contact a cluster."""
import argparse
from pathlib import Path
import re
import subprocess
import sys

import yaml


ALLOWED = {"Namespace", "GitRepository", "Kustomization", "HelmRelease", "HelmRepository", "OCIRepository"}
FLUX_APIS = {"GitRepository": "source.toolkit.fluxcd.io/v1", "Kustomization": "kustomize.toolkit.fluxcd.io/v1", "HelmRelease": "helm.toolkit.fluxcd.io/v2"}


def command(*argv):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise ValueError("declaration checkout or build command failed")
    return result.stdout.strip()


def inside(checkout, relative):
    path = (checkout / relative).resolve()
    if not path.is_relative_to(checkout) or path == checkout or not path.is_dir():
        raise ValueError("declaration path must be a directory inside the checkout")
    return path


def identity(doc):
    meta = doc["metadata"]
    return doc["kind"], meta.get("namespace", ""), meta["name"]


def documents(output):
    docs = list(yaml.safe_load_all(output))
    for doc in docs:
        if not isinstance(doc, dict) or doc.get("kind") not in ALLOWED:
            raise ValueError("bootstrap accepts only Flux declarations and namespaces")
        if doc["kind"] in FLUX_APIS:
            if doc.get("apiVersion") != FLUX_APIS[doc["kind"]] or doc.get("spec", {}).get("suspend") is not True:
                raise ValueError("every GitRepository, Flux Kustomization and HelmRelease must be suspended")
        identity(doc)
    return docs


def build(checkout, commit, root_path, root_name, source_name, namespace, git_url, runner=command):
    checkout = Path(checkout).resolve()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("a full immutable checkout commit is required")
    if runner("git", "-C", str(checkout), "rev-parse", "HEAD") != commit:
        raise ValueError("checkout HEAD differs from expected commit")
    if runner("git", "-C", str(checkout), "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("bootstrap checkout must be clean")
    root_dir = inside(checkout, root_path)
    docs = documents(runner("kubectl", "kustomize", str(root_dir)))
    indexed = {}

    def add(items):
        for item in items:
            key = identity(item)
            if key in indexed and indexed[key] != item:
                raise ValueError("conflicting duplicate resource in declaration graph")
            indexed[key] = item

    add(docs)
    root = indexed.get(("Kustomization", namespace, root_name))
    if not root or inside(checkout, root["spec"]["path"]) != root_dir:
        raise ValueError("expected root declaration is missing or has a different path")
    if root["spec"].get("sourceRef") != {"kind": "GitRepository", "name": source_name}:
        raise ValueError("root must reference the declared bootstrap source")
    sources = {doc["metadata"]["name"]: doc for doc in docs if doc["kind"] == "GitRepository"}
    if source_name not in sources:
        raise ValueError("bootstrap source is missing")
    for source in sources.values():
        if source["metadata"].get("namespace") != namespace or source["spec"].get("url") != git_url:
            raise ValueError("sources must use the expected repository and Flux namespace")
        ref = source["spec"].get("ref", {})
        if set(ref) == {"commit"}:
            resolved = ref["commit"]
        elif set(ref) == {"tag"} and re.fullmatch(r"v[0-9][A-Za-z0-9.+-]*", ref["tag"]):
            resolved = runner("git", "-C", str(checkout), "rev-parse", "refs/tags/" + ref["tag"] + "^{commit}")
        else:
            raise ValueError("initial sources require a stable immutable tag or commit")
        if resolved != commit:
            raise ValueError("all initial source pins must resolve to the reviewed bootstrap commit")
    children = [doc for doc in docs if doc["kind"] == "Kustomization" and identity(doc) != identity(root)]
    if not children:
        raise ValueError("at least one independent child unit is required")
    used_sources = set()
    for child in children:
        ref = child["spec"].get("sourceRef", {})
        name = ref.get("name")
        if child["metadata"].get("namespace") != namespace or ref.get("kind") != "GitRepository" or name not in sources:
            raise ValueError("child must reference a declared unit configuration source")
        if name == source_name or name in used_sources:
            raise ValueError("each child requires its own independent configuration source")
        used_sources.add(name)
        leaf = documents(runner("kubectl", "kustomize", str(inside(checkout, child["spec"]["path"]))))
        if any(doc["kind"] in {"Kustomization", "GitRepository"} for doc in leaf):
            raise ValueError("unit paths cannot redefine bootstrap or sibling controllers")
        if not any(doc["kind"] == "HelmRelease" for doc in leaf):
            raise ValueError("each release unit must declare its paused HelmRelease")
        add(leaf)
    if used_sources != set(sources) - {source_name}:
        raise ValueError("unit source declarations must map one-to-one to children")
    return list(indexed.values())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ("checkout", "commit", "root-path", "root-name", "source", "namespace", "git-url"):
        parser.add_argument("--" + field, required=True)
    args = parser.parse_args()
    try:
        result = build(args.checkout, args.commit, args.root_path, args.root_name, args.source, args.namespace, args.git_url)
        sys.stdout.write(yaml.safe_dump_all(result, sort_keys=False))
    except (ValueError, KeyError, TypeError, yaml.YAMLError, subprocess.TimeoutExpired):
        raise SystemExit("paused Flux declaration validation failed; no declarations emitted") from None


if __name__ == "__main__":
    main()
