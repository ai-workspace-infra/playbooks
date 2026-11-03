#!/usr/bin/env python3
"""Reconcile selected GitHub OIDC declarations from an exact GitOps commit."""
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import yaml


ROLE_NAME = re.compile(r"github-actions-[a-z0-9-]+\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")
POLICY_BLOCK = re.compile(
    r'path\s+("(?:[^"\\]|\\.)*")\s*\{\s*capabilities\s*=\s*(\[[^\]]*\])\s*\}',
    re.S,
)


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    return result.stdout.strip()


def read_declaration(root: Path, category: str, name: str, kind: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._%+@-]*[A-Za-z0-9]", name):
        raise ValueError("invalid GitOps declaration name")
    path = root / "security" / "vault" / category / f"{name}.yaml"
    relative = path.relative_to(root).as_posix()
    try:
        git(root, "ls-files", "--error-unmatch", relative)
    except subprocess.CalledProcessError as error:
        raise ValueError("selected GitOps declaration is not tracked at the pinned commit") from error
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ValueError("selected GitOps Vault declaration could not be read") from error
    if (not isinstance(value, dict)
            or value.get("apiVersion") != "security.gitops/v1alpha1"
            or value.get("kind") != kind
            or value.get("metadata", {}).get("name") != name
            or not isinstance(value.get("spec"), dict)):
        raise ValueError("selected GitOps Vault declaration has an invalid schema")
    return value


def render_policy(spec: dict) -> tuple[str, list[dict]]:
    paths = spec.get("paths")
    if not isinstance(paths, list) or not paths:
        raise ValueError("Vault ACL declaration must contain paths")
    blocks = []
    for item in paths:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise ValueError("Vault ACL path declaration is invalid")
        capabilities = item.get("capabilities")
        if not isinstance(capabilities, list) or not capabilities or any(not isinstance(c, str) for c in capabilities):
            raise ValueError("Vault ACL capabilities are invalid")
        blocks.append(
            f"path {json.dumps(item['path'])} {{\n"
            f"  capabilities = [{', '.join(json.dumps(cap) for cap in capabilities)}]\n"
            "}"
        )
    return "\n\n".join(blocks) + "\n", paths


def load_gitops_contract(config: dict) -> tuple[dict, dict, str, dict]:
    source = config.get("gitops")
    if not isinstance(source, dict):
        raise ValueError("exact GitOps checkout, SHA and role name are required")
    root = Path(source.get("root", "")).resolve(strict=True)
    expected_sha = source.get("sha", "")
    role_name = source.get("role_name", "")
    if not SHA.fullmatch(expected_sha) or not ROLE_NAME.fullmatch(role_name):
        raise ValueError("exact GitOps SHA and a declared GitHub role name are required")
    remote = git(root, "remote", "get-url", "origin")
    if remote not in {
        "https://github.com/ai-workspace-infra/gitops",
        "https://github.com/ai-workspace-infra/gitops.git",
        "git@github.com:ai-workspace-infra/gitops",
        "git@github.com:ai-workspace-infra/gitops.git",
    }:
        raise ValueError("GitOps checkout origin is not the approved repository")
    actual_sha = git(root, "rev-parse", "HEAD")
    if actual_sha != expected_sha:
        raise ValueError("GitOps checkout does not match the supplied commit SHA")
    if git(root, "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("GitOps checkout has tracked changes")

    role_doc = read_declaration(root, "roles", role_name, "VaultJWTAuthRole")
    role = role_doc["spec"]
    policy_names = role.get("token_policies")
    if not isinstance(policy_names, list) or not policy_names or len(policy_names) > 10:
        raise ValueError("selected GitOps role must reference a bounded policy set")
    policies, policy_paths = {}, {}
    for name in policy_names:
        policy_doc = read_declaration(root, "policies", name, "VaultACLPolicy")
        policies[name], policy_paths[name] = render_policy(policy_doc["spec"])
    return {role_name: role}, policies, actual_sha, policy_paths


def policy_is_exact_read_only(policy: str) -> bool:
    matches = list(POLICY_BLOCK.finditer(policy))
    remainder = POLICY_BLOCK.sub("", policy).strip()
    if not matches or remainder:
        return False
    for match in matches:
        try:
            path = json.loads(match.group(1))
            capabilities = json.loads(match.group(2))
        except json.JSONDecodeError:
            return False
        if not isinstance(path, str) or "*" in path or "+" in path or capabilities != ["read"]:
            return False
    return True


def workflow_claims(claims: dict) -> list[str]:
    values = []
    for key in ("workflow_ref", "job_workflow_ref"):
        value = claims.get(key)
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, list) and value and all(isinstance(item, str) for item in value):
            values.extend(value)
        elif value is not None:
            raise ValueError("GitHub workflow claims must be strings")
    if not values:
        raise ValueError("an exact workflow allowlist is required")
    for value in values:
        workflow, separator, ref = value.partition("@")
        if not separator or "/.github/workflows/" not in workflow or "*" in workflow:
            raise ValueError("wildcard or incomplete workflow names are forbidden")
    return values


def validate_role(name: str, role: dict, policies: dict, policy_paths: dict) -> None:
    claims = role.get("bound_claims", {})
    if not ROLE_NAME.fullmatch(name):
        raise ValueError("role name is outside the GitHub Actions namespace")
    if (not isinstance(claims, dict)
            or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", claims.get("repository", ""))
            or not claims.get("ref")):
        raise ValueError("repository and ref bindings are required")
    workflows = workflow_claims(claims)
    if (role.get("user_claim") != "sub" or role.get("role_type") != "jwt"
            or role.get("bound_audiences") != ["vault"]
            or role.get("token_no_default_policy") is not True):
        raise ValueError("JWT audience and no-default-policy contract required")
    if role.get("token_type") != "batch":
        raise ValueError("short-lived batch identity required")
    ttl_pair = (role.get("token_ttl"), role.get("token_max_ttl"))
    if ttl_pair not in {("30m", "30m"), ("1h", "1h")}:
        raise ValueError("JWT TTL must be 30m or 1h with a matching maximum")
    if role.get("token_explicit_max_ttl", role.get("token_max_ttl")) != role.get("token_max_ttl"):
        raise ValueError("explicit maximum TTL must match the role maximum")
    policy_names = role.get("token_policies")
    if (not isinstance(policy_names, list) or not policy_names
            or any(policy not in policies for policy in policy_names)):
        raise ValueError("all referenced policies must come from the selected GitOps commit")

    refs = claims["ref"] if isinstance(claims["ref"], list) else [claims["ref"]]
    is_prod = claims.get("environment") == "prod" or name.endswith("-prod") or "-prod-" in name
    if is_prod and refs == ["refs/heads/main"]:
        if (claims.get("environment") != "prod"
                or claims.get("ref_protected") not in (True, "true")
                or ttl_pair != ("30m", "30m")
                or any("*" in item or not item.endswith("@refs/heads/main") for item in workflows)
                or any(not policy_is_exact_read_only(policies[policy]) for policy in policy_names)):
            raise ValueError("PROD main roles require a protected exact workflow, 30m TTL and exact read-only policies")
        for policy in policy_names:
            for path in policy_paths[policy]:
                if path.get("capabilities") != ["read"] or "*" in path.get("path", "") or "+" in path.get("path", ""):
                    raise ValueError("PROD main roles may read only exact declared KV paths")
    elif is_prod and any(ref not in ("refs/tags/v*", "refs/heads/release/v*") for ref in refs):
        raise ValueError("PROD release roles must use stable release tags or protected release lines")


def resolve(config: dict) -> tuple[dict, dict, str]:
    roles, policies, source_sha, policy_paths = load_gitops_contract(config)
    for name, role in roles.items():
        validate_role(name, role, policies, policy_paths)
    referenced = {policy for role in roles.values() for policy in role["token_policies"]}
    if referenced != set(policies):
        raise ValueError("only policies referenced by the selected role may be reconciled")
    return roles, policies, source_sha


def equivalent(actual, desired):
    def norm(value):
        if value == "1h":
            return 3600
        if value == "30m":
            return 1800
        if isinstance(value, list):
            return sorted(value)
        return value
    return all(norm(actual.get(key)) == norm(value) for key, value in desired.items())


def reconcile(config):
    if not config.get("address", "").startswith("https://") or not config.get("token"):
        raise ValueError("explicit HTTPS address and runtime Vault identity required")
    roles, policies, source_sha = resolve(config)
    base = config["address"].rstrip("/") + "/v1/"

    def request(path, payload=None):
        req = Request(base + path, data=None if payload is None else json.dumps(payload).encode(),
                      headers={"X-Vault-Token": config["token"], "Content-Type": "application/json"})
        try:
            with urlopen(req, timeout=20) as response:
                body = response.read()
                return json.loads(body).get("data", {}) if body else {}
        except HTTPError as error:
            if error.code == 404 and payload is None:
                return None
            raise RuntimeError("Vault operation failed with HTTP " + str(error.code)) from None

    if "jwt/" not in request("sys/auth"):
        raise ValueError("JWT mount must be established before role reconciliation")
    desired_config = {"oidc_discovery_url": "https://token.actions.githubusercontent.com",
                      "bound_issuer": "https://token.actions.githubusercontent.com"}
    pending = []
    if not equivalent(request("auth/jwt/config") or {}, desired_config):
        pending.append(("auth/jwt/config", desired_config))
    for name, policy in policies.items():
        if (request("sys/policies/acl/" + name) or {}).get("policy", "").strip() != policy.strip():
            pending.append(("sys/policies/acl/" + name, {"policy": policy}))
    for name, role in roles.items():
        desired = {key: value for key, value in role.items() if key not in ("description", "role_name")}
        if not equivalent(request("auth/jwt/role/" + name) or {}, desired):
            pending.append(("auth/jwt/role/" + name, desired))
    for path, desired in pending:
        if config.get("apply") is True:
            request(path, desired)
    return {"status": "applied" if config.get("apply") is True else "preview",
            "roles": len(roles), "policies": len(policies), "changed": len(pending),
            "gitops_sha": source_sha,
            "issuer": desired_config["bound_issuer"]}


if __name__ == "__main__":
    try:
        print(json.dumps(reconcile(json.load(sys.stdin))))
    except Exception as error:
        print("Vault GitHub OIDC reconciliation stopped: " + type(error).__name__, file=sys.stderr)
        sys.exit(1)
