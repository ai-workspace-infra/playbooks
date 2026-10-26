#!/usr/bin/env python3
"""Narrow Dev bootstrap adapter; credentials stay in memory/ssh-agent."""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import urllib.request

TARGET = "dev-k3s-v5"
ROLE = "github-actions-playbooks-dev-k3s-bootstrap"
VAULT_RECORD = "kv/data/CICD/dev/k3s-bootstrap"
REPOSITORY = "https://github.com/ai-workspace-infra/gitops.git"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def request(url, headers=None, data=None):
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}, data=data), timeout=30) as response:
        return json.load(response) if response.status != 204 else {}


def validate_session(session):
    require(isinstance(session, dict), "invalid Vault auth response")
    require(0 < session.get("lease_duration", 0) <= 1200, "Vault session TTL is not bounded")
    require(session.get("token_type") in {"service", "batch"} and session.get("client_token"), "invalid Vault token type")
    for field in ("policies", "token_policies", "identity_policies"):
        policies = session.get(field, [])
        require(isinstance(policies, list) and "root" not in policies, "root Vault session is forbidden")
    return session


def validate_cmdb(data, instance_id):
    require(isinstance(data, dict) and set(data) == {TARGET}, "CMDB must contain exactly the Dev target")
    h = data[TARGET]
    require(isinstance(h, dict), "invalid CMDB host")
    require(str(h.get("instance_id")) == instance_id and instance_id.isdecimal() and int(instance_id) > 0, "instance ID mismatch")
    require(h.get("name") == TARGET and h.get("label") == TARGET, "CMDB target identity mismatch")
    ipaddress.IPv4Address(h["ip"])
    require("dev" in h.get("groups", []) and not {"prod", "production", "uat"}.intersection(h.get("groups", [])), "CMDB environment mismatch")
    v = h.get("host_vars", {})
    require(v.get("cloud_provider") == "akamai-cloud" and v.get("cloud_region") == "jp-tyo-3", "CMDB provider/region mismatch")
    require(v.get("k3s_platform_node_name") == TARGET and v.get("k3s_platform_write_kubeconfig_mode") == "0600", "missing Dev node/security inputs")
    # Preserve only the approved role inputs; arbitrary ansible_* connection or
    # interpreter overrides from an artifact must never become executable input.
    keys = ("cloud_provider", "cloud_region", "k3s_platform_k3s_version", "k3s_platform_node_name",
            "k3s_platform_cluster_name", "k3s_platform_write_kubeconfig_mode")
    require(re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+\+k3s[0-9]+", v.get("k3s_platform_k3s_version", "")), "invalid K3s version")
    return {TARGET: {"name": TARGET, "label": TARGET, "ip": h["ip"], "instance_id": h["instance_id"],
                     "groups": ["dev", "k3s"], "host_vars": {k: v[k] for k in keys if k in v}}}


def run(args, env, *, stdin=None):
    result = subprocess.run(args, input=stdin, text=True, capture_output=True, env=env, timeout=2400)
    require(result.returncode == 0, "bootstrap subprocess failed: " + Path(args[0]).name)
    return result.stdout


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--cmdb", required=True, type=Path)
    p.add_argument("--cmdb-sha256", required=True)
    p.add_argument("--instance-id", required=True)
    p.add_argument("--gitops-sha", required=True)
    p.add_argument("--receipt", required=True, type=Path)
    a = p.parse_args()
    require(re.fullmatch(r"[0-9a-f]{40}", a.gitops_sha), "GitOps commit must be a full SHA")
    require(re.fullmatch(r"[0-9a-f]{64}", a.cmdb_sha256), "CMDB digest must be sha256")
    require(hashlib.sha256(a.cmdb.read_bytes()).hexdigest() == a.cmdb_sha256, "CMDB checksum mismatch")
    cmdb = validate_cmdb(json.loads(a.cmdb.read_text()), a.instance_id)
    a.receipt.unlink(missing_ok=True)
    oidc = request(os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"] + "&audience=vault",
                   {"Authorization": "Bearer " + os.environ["ACTIONS_ID_TOKEN_REQUEST_TOKEN"]})["value"]
    session = request("https://vault.svc.plus/v1/auth/jwt/login", {"Content-Type": "application/json"},
                      json.dumps({"role": ROLE, "jwt": oidc}).encode())["auth"]
    validate_session(session)
    token = session["client_token"]
    agent_env = None
    try:
        secret = request("https://vault.svc.plus/v1/" + VAULT_RECORD, {"X-Vault-Token": token})["data"]["data"]
        if session.get("token_type") != "batch":
            request("https://vault.svc.plus/v1/auth/token/revoke-self", {"X-Vault-Token": token}, b"{}")
        token = None
        require(all(secret.get(k) for k in ("SSH_PRIVATE_KEY", "SSH_KNOWN_HOSTS", "SSH_USER")), "Dev SSH binding is incomplete")
        require(re.fullmatch(r"[a-z_][a-z0-9_-]*", secret["SSH_USER"]), "invalid management SSH user")
        with tempfile.TemporaryDirectory(prefix="dev-bootstrap-") as temporary:
            d = Path(temporary)
            known_hosts = d / "known_hosts"
            known_hosts.write_text(secret["SSH_KNOWN_HOSTS"] + "\n")
            known_hosts.chmod(0o600)
            run(["ssh-keygen", "-F", cmdb[TARGET]["ip"], "-f", str(known_hosts)], os.environ.copy())
            agent = run(["ssh-agent", "-s"], os.environ.copy())
            values = {k: re.search(rf"{k}=([^;]+);", agent).group(1) for k in ("SSH_AUTH_SOCK", "SSH_AGENT_PID")}
            agent_env = {**os.environ, **values}
            run(["ssh-add", "-"], agent_env, stdin=secret["SSH_PRIVATE_KEY"] + "\n")
            cmdb[TARGET]["ansible_user"] = secret["SSH_USER"]
            narrow_cmdb = d / "cmdb.json"
            narrow_cmdb.write_text(json.dumps(cmdb))
            narrow_cmdb.chmod(0o600)
            receipt = d / "receipt.json"
            variables = d / "inputs.json"
            variables.write_text(json.dumps({"k3s_platform_git_url": REPOSITORY, "k3s_platform_git_auth_mode": "public",
                "k3s_platform_strict_dev_receipt": True, "k3s_platform_git_commit": a.gitops_sha, "k3s_platform_flux_root_path": "./environments/clusters/" + TARGET,
                "k3s_platform_receipt_path": str(receipt)}))
            ssh_args = f"-o StrictHostKeyChecking=yes -o UserKnownHostsFile={known_hosts} -o PasswordAuthentication=no -o BatchMode=yes"
            env = {**agent_env, "AI_WORKSPACE_CMDB_JSON": str(narrow_cmdb), "ANSIBLE_HOST_KEY_CHECKING": "True",
                   "ANSIBLE_SSH_ARGS": ssh_args, "ANSIBLE_ROLES_PATH": str(Path("roles").resolve())}
            inventory = "inventory/terraform_cmdb.py"
            observed = json.loads(run(["ansible-inventory", "-i", inventory, "--list"], env))
            require(observed.get("k3s", {}).get("hosts") == [TARGET], "dynamic inventory selection mismatch")
            run(["ansible-inventory", "-i", inventory, "--host", TARGET], env)
            run(["ansible-inventory", "-i", inventory, "--graph"], env)
            playbook = ["ansible-playbook", "-i", inventory, "k3s_platform_bootstrap_with_gitops.yml", "--limit", TARGET, "-e", "@" + str(variables)]
            run([*playbook, "--syntax-check"], env)
            run(["ansible", "-i", inventory, TARGET, "-m", "ping"], env)
            run(playbook, env)
            result = json.loads(receipt.read_text())
            require(result.get("result") == "PASS" and result.get("node") == TARGET, "missing runtime receipt")
            result.update(instance_id=a.instance_id, cmdb_sha256=a.cmdb_sha256, gitops_sha=a.gitops_sha,
                          playbooks_sha=run(["git", "rev-parse", "HEAD"], env).strip(),
                          run_id=os.environ.get("GITHUB_RUN_ID"), environment="dev")

    finally:
        if agent_env:
            subprocess.run(["ssh-agent", "-k"], env=agent_env, capture_output=True)
        if token and session.get("token_type") != "batch":
            request("https://vault.svc.plus/v1/auth/token/revoke-self", {"X-Vault-Token": token}, b"{}")
    a.receipt.parent.mkdir(parents=True, exist_ok=True)
    a.receipt.write_text(json.dumps(result, sort_keys=True) + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Never include remote responses, command output or secret values.
        raise SystemExit("Dev bootstrap failed (" + type(error).__name__ + "); no PASS receipt issued") from None
