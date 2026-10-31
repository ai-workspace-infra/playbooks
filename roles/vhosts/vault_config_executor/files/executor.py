#!/usr/bin/env python3
"""Vault configuration executor (Playbooks owner).

Reads one JSON request on stdin and prints a redacted JSON receipt.
Operations (``operation``):
  ssh_ca        SSH secrets mount + CA + signing role (never regenerates a CA)
  jwt_contract  one JWT role and its ACL policy from a reviewed contract
  kv_fields     set selected KV v2 fields (CAS; PUT only when path is absent)

Default is preview (``apply`` false): every operation reads live state and
reports the pending changes without writing. Values of KV fields, tokens and
CA private material are never printed.
"""
import json
import re
import sys
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ENVIRONMENTS = {"sit", "uat", "prod"}
NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
KV_PATH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./-]{0,200}$")
FIELD = re.compile(r"^[A-Z][A-Z0-9_]{0,80}$")

# KV writes are confined to the selected environment's prefix. Legacy shared
# layouts are only allowed through this reviewed, exact (environment, mount,
# path) list; there is deliberately no caller-controlled bypass flag.
ENV_KV_PREFIX = {"sit": "sit/", "uat": "uat/", "prod": "prod/"}
REVIEWED_SHARED_KV_PATHS = frozenset()  # e.g. {("prod", "kv", "shared/x")} after owner review
REMOVED_KV_KEYS = ("allow_shared_path",)


def strict_bool(req, key, default=False):
    value = req.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a JSON boolean")
    return value


class Vault:
    def __init__(self, address, token, timeout=20):
        url = urlsplit(address)
        if (not url.hostname or url.username or url.password or url.path not in ('', '/')
                or url.query or url.fragment or
                not (url.scheme == 'https' or (url.scheme == 'http' and url.hostname == '127.0.0.1'))):
            raise ValueError("explicit HTTPS Vault address required")
        if not token:
            raise ValueError("runtime Vault identity required")
        self.base = address.rstrip("/") + "/v1/"
        self.token = token
        self.timeout = timeout

    def call(self, method, path, payload=None, missing_ok=False):
        data = None if payload is None else json.dumps(payload).encode()
        req = Request(self.base + path, data=data, method=method,
                      headers={"X-Vault-Token": self.token, "Content-Type": "application/json"})
        try:
            with urlopen(req, timeout=self.timeout) as res:
                body = res.read()
                return json.loads(body) if body else {}
        except HTTPError as exc:
            if exc.code == 404 and missing_ok:
                return None
            raise RuntimeError(f"Vault {method} {path.split('/')[0]} failed with HTTP {exc.code}") from None

    def read(self, path):
        body = self.call("GET", path, missing_ok=True)
        return None if body is None else body.get("data", {})


def common(req):
    if req.get("environment") not in ENVIRONMENTS:
        raise ValueError("environment must be one of sit/uat/prod")
    strict_bool(req, "apply")
    if req.get("apply") and req.get("confirm") != f"APPLY-VAULT-{req['operation'].upper()}-{req['environment'].upper()}":
        raise ValueError("apply requires the exact confirmation token")


# ---- ssh_ca -----------------------------------------------------------------
def ssh_ca(vault, req):
    mount, role, principal = req.get("mount", "ssh-client-signer"), req["role"], req["principal"]
    ttl = int(req.get("ttl_seconds", 1800))
    if not NAME.match(mount) or not NAME.match(role) or not re.match(r"^[a-z_][a-z0-9_-]{0,31}$", principal):
        raise ValueError("invalid mount, role or principal")
    if not 60 <= ttl <= 3600:
        raise ValueError("certificate ttl must be between 1m and 1h")
    desired = {"key_type": "ca", "allow_user_certificates": True, "allowed_users": principal,
               "default_user": principal, "allowed_extensions": "", "default_extensions": {},
               "ttl": ttl, "max_ttl": ttl, "algorithm_signer": "default"}
    pending = []
    mounts = vault.read("sys/mounts") or {}
    if f"{mount}/" not in mounts:
        pending.append(("POST", f"sys/mounts/{mount}", {"type": "ssh"}, "mount"))
        has_ca = False
    else:
        has_ca = bool((vault.read(f"{mount}/config/ca") or {}).get("public_key"))
    if not has_ca:
        pending.append(("POST", f"{mount}/config/ca", {"generate_signing_key": True, "key_type": "ed25519"}, "ca"))
    current = (vault.read(f"{mount}/roles/{role}") or {}) if has_ca else {}
    if any(current.get(k) != v for k, v in desired.items() if k != "default_extensions"):
        pending.append(("POST", f"{mount}/roles/{role}", desired, "role"))
    if req.get("apply"):
        for method, path, payload, _ in pending:
            vault.call(method, path, payload)
    public_key = (vault.read(f"{mount}/config/ca") or {}).get("public_key", "") if (has_ca or req.get("apply")) else ""
    return {"pending": [p[3] for p in pending], "ca_public_key": public_key}


# ---- jwt_contract -------------------------------------------------------------
def canonical_contract(contract):
    """Convert parsed GitOps YAML declarations to the Vault API contract."""
    if 'role_declaration' not in contract and 'policy_declaration' not in contract:
        return contract
    role_doc, policy_doc = contract['role_declaration'], contract['policy_declaration']
    if (role_doc.get('apiVersion') != 'security.gitops/v1alpha1' or
            role_doc.get('kind') != 'VaultJWTAuthRole' or
            policy_doc.get('apiVersion') != 'security.gitops/v1alpha1' or
            policy_doc.get('kind') != 'VaultACLPolicy'):
        raise ValueError('unsupported GitOps Vault declaration')
    name = role_doc.get('metadata', {}).get('name')
    if not name or policy_doc.get('metadata', {}).get('name') != name:
        raise ValueError('role and policy names must match')
    paths = policy_doc.get('spec', {}).get('paths')
    if not isinstance(paths, list) or not paths:
        raise ValueError('GitOps policy must contain explicit paths')
    rules = {}
    for entry in paths:
        if not isinstance(entry, dict) or set(entry) != {'path', 'capabilities'}:
            raise ValueError('unsupported GitOps policy path fields')
        path, capabilities = entry['path'], entry['capabilities']
        if not isinstance(path, str) or not path or path in rules or not isinstance(capabilities, list) or not capabilities:
            raise ValueError('invalid or duplicate GitOps policy path')
        rules[path] = {'capabilities': capabilities}
    return {'role_name': name, 'role': role_doc.get('spec', {}), 'policy': {'path': rules}}


def jwt_contract(vault, req):
    contract = canonical_contract(req["contract"])
    name, role, policy = contract["role_name"], contract["role"], contract["policy"]
    if not NAME.match(name):
        raise ValueError("invalid role name")
    claims = role.get("bound_claims") or {}
    workflows = [claims[k] for k in ('workflow_ref', 'job_workflow_ref') if claims.get(k)]
    if not claims.get("ref") or not workflows:
        raise ValueError("ref and exact workflow_ref or job_workflow_ref bindings are required")
    for workflow in workflows:
        for ref in workflow if isinstance(workflow, list) else [workflow]:
            if (not isinstance(ref, str) or '@' not in ref or
                    any(c in ref.split('@', 1)[0] for c in '*?[]')):
                raise ValueError("wildcard or invalid workflow names are forbidden")
    allowed = set(req.get("allowed_capabilities", ["read"]))
    for path, rule in (policy.get("path") or {}).items():
        if not set(rule.get("capabilities", [])) <= allowed:
            raise ValueError(f"policy capability outside the allowed set for {path}")
    if not policy.get("path"):
        raise ValueError("policy must list explicit paths")
    if not isinstance(role.get('token_policies'), list) or set(role['token_policies']) != {name}:
        raise ValueError("role must reference its own policy only")
    mount = req.get("auth_mount", "jwt")
    if not NAME.fullmatch(mount):
        raise ValueError("invalid auth mount")
    pending = []
    policy_text = json.dumps(policy, sort_keys=True)
    current_policy = (vault.read(f"sys/policies/acl/{name}") or {}).get("policy")
    try:
        same_policy = current_policy is not None and json.loads(current_policy) == policy
    except ValueError:
        same_policy = False
    if not same_policy:
        pending.append(("PUT", f"sys/policies/acl/{name}", {"policy": policy_text}, "policy"))
    current = vault.read(f"auth/{mount}/role/{name}") or {}

    def norm(v, field):
        if field in ('token_ttl', 'token_max_ttl', 'token_explicit_max_ttl'):
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return int(v)
            if isinstance(v, str):
                if v.isdecimal():
                    return int(v)
                parts = re.findall(r'(\d+)(h|m|s)', v)
                if parts and ''.join(a + b for a, b in parts) == v:
                    return sum(int(n) * {'h': 3600, 'm': 60, 's': 1}[unit] for n, unit in parts)
        return sorted(v) if isinstance(v, list) else v
    if any(norm(current.get(k), k) != norm(v, k) for k, v in role.items()):
        pending.append(("POST", f"auth/{mount}/role/{name}", role, "role"))
    if req.get("apply"):
        for method, path, payload, _ in pending:
            vault.call(method, path, payload)
    return {"role": name, "pending": [p[3] for p in pending]}


# ---- kv_fields ----------------------------------------------------------------
def kv_fields(vault, req):
    mount, path, fields = req.get("mount", "kv"), req["path"], req["fields"]
    if not NAME.match(mount) or not KV_PATH.match(path) or ".." in path:
        raise ValueError("invalid KV mount or path")
    for key in REMOVED_KV_KEYS:
        if key in req:
            raise ValueError(f"{key} is not supported; shared paths need a reviewed exact exception")
    env = req["environment"]
    if not path.startswith(ENV_KV_PREFIX[env]) and (env, mount, path) not in REVIEWED_SHARED_KV_PATHS:
        raise ValueError("KV path must live under the selected environment")
    for other, prefix in ENV_KV_PREFIX.items():
        if other != env and path.startswith(prefix):
            raise ValueError("cross-environment KV write rejected")
    if not fields or not all(FIELD.match(k) and isinstance(v, str) and v for k, v in fields.items()):
        raise ValueError("explicit non-empty field values required")
    overwrite = strict_bool(req, "overwrite")
    current = vault.read(f"{mount}/data/{path}")
    data = (current or {}).get("data") or {}
    version = ((current or {}).get("metadata") or {}).get("version", 0)
    pending, preserved = [], []
    for key, value in fields.items():
        if key not in data:
            pending.append(key)
        elif data[key] != value:
            (pending if overwrite else preserved).append(key)
    if req.get("apply") and pending:
        merged = dict(data)
        merged.update({k: fields[k] for k in pending})
        vault.call("POST", f"{mount}/data/{path}", {"options": {"cas": version}, "data": merged})
    return {"path": f"{mount}/data/{path}", "created": current is None, "pending": sorted(pending),
            "preserved_existing": sorted(preserved)}


OPERATIONS = {"ssh_ca": ssh_ca, "jwt_contract": jwt_contract, "kv_fields": kv_fields}


def run(req, vault=None):
    op = req.get("operation")
    if op not in OPERATIONS:
        raise ValueError("unknown operation")
    common(req)
    vault = vault or Vault(req.get("address", ""), req.get("token", ""))
    result = OPERATIONS[op](vault, req)
    result.update({"operation": op, "environment": req["environment"], "applied": bool(req.get("apply")),
                   "changed": len(result.get("pending", [])) if req.get("apply") else 0})
    return result


def main():
    try:
        print(json.dumps(run(json.load(sys.stdin)), sort_keys=True))
    except (ValueError, KeyError, RuntimeError) as exc:
        msg = str(exc) if not isinstance(exc, KeyError) else f"missing input {exc}"
        print(json.dumps({"error": msg}))
        sys.exit(1)


if __name__ == "__main__":
    main()
