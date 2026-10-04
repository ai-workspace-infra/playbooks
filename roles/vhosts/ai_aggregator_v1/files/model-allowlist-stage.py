#!/usr/bin/env python3
"""Add the GitOps public model policy to a staged Home-Lab APISIX bundle.

The existing bootstrap owns credentials and base routes. This program runs
after that bootstrap on every APISIX start. It never prints configuration or
credential values, and writes only to the node's runtime filesystem.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import tempfile
from pathlib import Path

import yaml

MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


def atomic_yaml(path: Path, value: dict) -> bool:
    old = yaml.safe_load(path.read_text())
    if old == value:
        return False
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        original = path.stat()
        os.fchmod(fd, original.st_mode & 0o777)
        os.fchown(fd, original.st_uid, original.st_gid)
        with os.fdopen(fd, "w") as stream:
            stream.write(yaml.safe_dump(value, sort_keys=False))
            if path.name == "apisix.yaml":
                stream.write("#END\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return True


def apply_policy(bundle: dict, runtime: dict, models: list[str]) -> tuple[dict, dict]:
    routes = bundle.get("routes", [])
    base = next((r for r in routes if r.get("id") == "new-api"), None)
    if not base or base.get("uri") != "/*":
        raise ValueError("expected Home-Lab New API catch-all route is absent")
    for required in ("key-auth", "consumer-restriction", "ip-restriction", "proxy-rewrite"):
        if required not in base.get("plugins", {}):
            raise ValueError(f"base route lacks {required}")

    catalog = {
        "object": "list",
        "success": True,
        "data": [
            {
                "id": model,
                "object": "model",
                "created": 0,
                "owned_by": (
                    "anthropic" if model.startswith("claude-")
                    else "google" if model.startswith("gemini-")
                    else "openai"
                ),
            }
            for model in models
        ],
    }
    models_route = copy.deepcopy(base)
    models_route.update(id="new-api-models-allowlist", uri="/v1/models", priority=100)
    models_route["plugins"]["response-rewrite"] = {
        # Rewriting an authentication error would expose the catalog and
        # change a 401 into 200. Only rewrite a successful upstream reply.
        "vars": [["status", "==", 200]],
        "body": json.dumps(catalog, separators=(",", ":")),
        "headers": {"set": {"Content-Type": "application/json"}},
    }

    inference_route = copy.deepcopy(base)
    inference_route.update(id="new-api-inference-allowlist", uri="/v1/*", priority=90)
    inference_route["plugins"]["request-validation"] = {
        "body_schema": {
            "type": "object",
            "properties": {"model": {"type": "string", "enum": models}},
            "required": ["model"],
        },
        "rejected_code": 403,
        "rejected_msg": "model is outside the published AI Gateway catalog",
    }

    bundle["routes"] = [
        r for r in routes
        if r.get("id") not in {models_route["id"], inference_route["id"]}
    ] + [models_route, inference_route]
    enabled_plugins = runtime.get("plugins")
    if not isinstance(enabled_plugins, list):
        raise ValueError("APISIX runtime plugin list is absent")
    for name in ("request-validation", "response-rewrite"):
        if name not in enabled_plugins:
            enabled_plugins.append(name)
    return bundle, runtime


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models-file", type=Path, required=True)
    parser.add_argument("--routes-file", type=Path, required=True)
    parser.add_argument("--runtime-file", type=Path, required=True)
    args = parser.parse_args()

    models = json.loads(args.models_file.read_text())
    if (
        not isinstance(models, list) or not models
        or any(not isinstance(m, str) or not MODEL_ID.fullmatch(m) for m in models)
        or len(set(models)) != len(models)
    ):
        raise SystemExit("invalid public model allowlist")
    bundle = yaml.safe_load(args.routes_file.read_text())
    runtime = yaml.safe_load(args.runtime_file.read_text())
    bundle, runtime = apply_policy(bundle, runtime, models)
    atomic_yaml(args.runtime_file, runtime)
    atomic_yaml(args.routes_file, bundle)
    print(f"staged AI Gateway model policy: {len(models)} public models")


if __name__ == "__main__":
    main()
