#!/usr/bin/env python3
"""Shared helpers for the k3s_platform_migration Ansible role.

All cluster hosts, users, domains, Vault KV paths, and business table lists are
loaded dynamically from GitOps manifests and Vault KV secrets. No IP addresses,
host credentials, tokens, or table names are hardcoded here.
"""

import argparse
import datetime
import ipaddress
import json
import os
import re
import subprocess
import tempfile
import time

try:
    import yaml
except ImportError:
    yaml = None

SQL_IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")

KUBE_RESOLVE_PREFIX = (
    "if [ -r /etc/rancher/k3s/k3s.yaml ]; then "
    "KUBE_CMD='k3s kubectl --kubeconfig=/etc/rancher/k3s/k3s.yaml'; "
    "else KUBE_CMD='kubectl'; fi"
)


def log(msg):
    """Print a timestamped status line to stdout."""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {msg}", flush=True)


def run_local(cmd, check=True, timeout=None):
    """Execute a local shell or argv command and return (stdout, stderr, rc)."""
    use_shell = isinstance(cmd, str)
    proc = subprocess.run(
        cmd,
        shell=use_shell,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"Command failed (code {proc.returncode}): {cmd}\nSTDERR:\n{proc.stderr.strip()}"
        )
    return proc.stdout.strip(), proc.stderr.strip(), proc.returncode


def default_repo_paths(script_file):
    """Resolve default GitOps manifest paths relative to the playbooks repository."""
    playbooks_dir = os.path.abspath(
        os.path.join(os.path.dirname(os.path.abspath(script_file)), "../../..")
    )
    gitops_root = os.path.abspath(os.path.join(playbooks_dir, "../gitops-canary-samples"))
    return {
        "source_manifest": os.path.join(
            gitops_root, "resources/svc.plus/prod/akamai/prod-k3s-v6.yaml"
        ),
        "target_manifest": os.path.join(
            gitops_root, "resources/svc.plus/prod/ulighthost/prod-k3s-v6-ulhost.yaml"
        ),
        "tables_manifest": os.path.join(
            gitops_root, "operations/accounts-migration/target-business.yaml"
        ),
    }


def get_vault_kv(path, field=None, retries=3):
    """Read a secret dictionary or single field from Vault KV v2 with retries."""
    if not path:
        raise ValueError("Vault KV path must not be empty")
    norm_path = path
    if norm_path.startswith("kv/data/"):
        norm_path = "kv/" + norm_path[len("kv/data/"):]
    elif not norm_path.startswith("kv/"):
        norm_path = "kv/" + norm_path

    cmd = ["vault", "kv", "get", "-format=json", norm_path]
    last_err = ""
    for attempt in range(retries):
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode == 0:
            data = json.loads(proc.stdout).get("data", {}).get("data", {})
            if field:
                if field not in data:
                    raise KeyError(
                        f"Field '{field}' not found in Vault KV path '{norm_path}'"
                    )
                return data[field]
            return data
        last_err = proc.stderr.strip()
        if attempt < retries - 1:
            time.sleep(2)
    raise RuntimeError(f"Vault KV read failed for '{norm_path}': {last_err}")


def _parse_yaml_or_fallback(path, mode):
    """Load YAML via PyYAML when available, or parse key blocks in stdlib."""
    if not os.path.isfile(path):
        raise FileNotFoundError(f"GitOps manifest not found: {path}")

    with open(path, "r", encoding="utf-8") as handle:
        content = handle.read()

    if yaml is not None:
        return yaml.safe_load(content) or {}

    if mode == "host":
        return _fallback_parse_host_yaml(content)
    if mode == "tables":
        return _fallback_parse_tables_yaml(content)
    raise ValueError(f"Unsupported YAML fallback mode: {mode}")


def _fallback_parse_host_yaml(content):
    """Minimal stdlib parser for GitOps host resource manifests."""
    def _extract(pattern, default=""):
        match = re.search(pattern, content)
        return match.group(1).strip().strip("\"'") if match else default

    aliases = {}
    service_domains = []
    section = None
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("reserved_dns_aliases:"):
            section = "aliases"
            continue
        if stripped.startswith("service_domains:"):
            section = "service_domains"
            continue
        if section and stripped and not line.startswith(" "):
            section = None
        if section == "aliases":
            if stripped.startswith("#") or not stripped:
                continue
            match = re.match(r"^\s+([a-zA-Z0-9\.\-]+):\s*([a-zA-Z0-9\.\-]+)", line)
            if match:
                aliases[match.group(1)] = match.group(2)
            elif re.match(r"^\s{6}[a-zA-Z0-9_]+:", line):
                section = None
        elif section == "service_domains":
            if stripped.startswith("#") or not stripped:
                continue
            match = re.match(r"^\s+-\s+([a-zA-Z0-9\.\-]+)", line)
            if match:
                service_domains.append(match.group(1))
            elif re.match(r"^\s{6}[a-zA-Z0-9_]+:", line):
                section = None

    cutover_raw = _extract(r"public_dns_cutover:\s*(true|false)", "false").lower()
    return {
        "hosts": [
            {
                "name": _extract(r"^\s*-\s*name:\s*([a-zA-Z0-9\.\-_]+)", ""),
                "public_ipv4": _extract(r"public_ipv4:\s*([0-9\.]+)", ""),
                "private_ipv4": _extract(r"private_ipv4:\s*([0-9\.]+)", ""),
                "ansible_user": _extract(r"ansible_user:\s*([a-zA-Z0-9_\-]+)", "root"),
                "host_vars": {
                    "k3s_platform_vault_ssh_key_path": _extract(
                        r"k3s_platform_vault_ssh_key_path:\s*([^\s#]+)", ""
                    ),
                    "k3s_platform_vault_ssh_key_field": _extract(
                        r"k3s_platform_vault_ssh_key_field:\s*([^\s#]+)",
                        "SSH_PRIVATE_KEY",
                    ),
                    "service_domains": service_domains,
                    "reserved_dns_aliases": aliases,
                    "public_dns_cutover": cutover_raw == "true",
                },
            }
        ]
    }


def _fallback_parse_tables_yaml(content):
    """Minimal stdlib parser for accounts-migration-bootstrap manifests."""
    def _extract(pattern, default=""):
        match = re.search(pattern, content, re.MULTILINE)
        return match.group(1).strip().strip("\"'") if match else default

    tables = []
    in_tables = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped == "tables:":
            in_tables = True
            continue
        if in_tables:
            if not stripped or stripped.startswith("#"):
                continue
            if line.startswith("- "):
                tables.append(stripped[2:].strip())
            elif not line.startswith(" "):
                in_tables = False
    return {
        "source_namespace": _extract(r"^source_namespace:\s*([^\s#]+)", "postgresql-prod"),
        "postgres_pod": _extract(r"^postgres_pod:\s*([^\s#]+)", "postgresql-postgresql-0"),
        "postgres_admin": _extract(r"^postgres_admin:\s*([^\s#]+)", "postgres"),
        "database_name": _extract(r"^database_name:\s*([^\s#]+)", "account"),
        "tables": tables,
    }


def parse_host_manifest(path):
    """Parse a GitOps host YAML manifest into normalized cluster host metadata."""
    raw = _parse_yaml_or_fallback(path, mode="host")
    hosts = raw.get("hosts") or []
    if not hosts:
        raise ValueError(f"No hosts declared in GitOps manifest: {path}")
    host_info = hosts[0]
    host_vars = host_info.get("host_vars") or {}
    return {
        "name": host_info.get("name", ""),
        "public_ipv4": host_info.get("public_ipv4", ""),
        "private_ipv4": host_info.get("private_ipv4", ""),
        "ansible_user": host_info.get("ansible_user", "root"),
        "ssh_key_path": host_vars.get("k3s_platform_vault_ssh_key_path", ""),
        "ssh_key_field": host_vars.get("k3s_platform_vault_ssh_key_field", "SSH_PRIVATE_KEY"),
        "service_domains": list(host_vars.get("service_domains") or []),
        "reserved_dns_aliases": dict(host_vars.get("reserved_dns_aliases") or {}),
        "public_dns_cutover": bool(host_vars.get("public_dns_cutover", False)),
    }


def validate_sql_identifier(name):
    """Ensure a PostgreSQL table/database/role identifier is safe for SQL interpolation."""
    if not isinstance(name, str) or not SQL_IDENTIFIER_RE.fullmatch(name):
        raise ValueError(f"Invalid SQL identifier: {name!r}")
    return name


def parse_tables_manifest(path):
    """Parse target-business.yaml and validate declared PostgreSQL identifiers."""
    raw = _parse_yaml_or_fallback(path, mode="tables")
    tables = raw.get("tables") or []
    if not tables:
        raise ValueError(f"No tables declared in operations manifest: {path}")
    validated_tables = [validate_sql_identifier(tbl) for tbl in tables]
    return {
        "namespace": raw.get("source_namespace") or "postgresql-prod",
        "postgres_pod": raw.get("postgres_pod") or "postgresql-postgresql-0",
        "postgres_admin": validate_sql_identifier(raw.get("postgres_admin") or "postgres"),
        "database_name": validate_sql_identifier(raw.get("database_name") or "account"),
        "tables": validated_tables,
    }


class HostConnection:
    """Context-managed SSH client authenticated via Vault KV ephemeral private key."""

    def __init__(self, host_cfg):
        self.host = host_cfg["public_ipv4"]
        if not self.host:
            raise ValueError("Host manifest missing public_ipv4")
        self.user = host_cfg.get("ansible_user") or "root"
        self.ssh_key_path = host_cfg.get("ssh_key_path") or ""
        self.ssh_key_field = host_cfg.get("ssh_key_field") or "SSH_PRIVATE_KEY"
        self._key_file = None

    def __enter__(self):
        if self.ssh_key_path:
            key_material = get_vault_kv(self.ssh_key_path, self.ssh_key_field)
            handle = tempfile.NamedTemporaryFile(mode="w", delete=False)
            handle.write(key_material.strip() + "\n")
            handle.flush()
            handle.close()
            os.chmod(handle.name, 0o600)
            self._key_file = handle.name
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._key_file and os.path.exists(self._key_file):
            try:
                os.unlink(self._key_file)
            except OSError:
                pass
            self._key_file = None

    def _base_ssh_args(self):
        args = [
            "ssh",
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=20",
        ]
        if self._key_file:
            args.extend(["-i", self._key_file])
        args.append(f"{self.user}@{self.host}")
        return args

    def run(self, remote_cmd, check=True, timeout=120, stdin_data=None):
        """Run a remote command on the host."""
        proc = subprocess.run(
            self._base_ssh_args() + [remote_cmd],
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if check and proc.returncode != 0:
            raise RuntimeError(
                f"SSH command failed on {self.user}@{self.host} (rc={proc.returncode}):\n"
                f"CMD: {remote_cmd}\nSTDERR:\n{proc.stderr.strip()}"
            )
        return proc.stdout.strip(), proc.stderr.strip(), proc.returncode

    def kubectl_exec(
        self,
        namespace,
        pod,
        inner_cmd,
        check=True,
        timeout=120,
        stdin_flag=False,
        stdin_data=None,
    ):
        """Execute a shell command inside a Kubernetes pod using K3s/standard kubectl."""
        exec_flag = "-i " if (stdin_flag or stdin_data is not None) else ""
        cmd = f"{KUBE_RESOLVE_PREFIX}; $KUBE_CMD exec {exec_flag}-n {namespace} {pod} -- {inner_cmd}"
        return self.run(cmd, check=check, timeout=timeout, stdin_data=stdin_data)


def detect_wireguard_ip(conn):
    """Discover the active WireGuard IPv4 address from a remote cluster host."""
    cmd = (
        "for iface in $(wg show interfaces 2>/dev/null); do "
        "ip -o -4 addr show \"$iface\" | awk '{print $4}' | cut -d/ -f1; "
        "done"
    )
    out, _, _ = conn.run(cmd)
    for line in out.splitlines():
        candidate = line.strip()
        try:
            ipaddress.IPv4Address(candidate)
            return candidate
        except ValueError:
            continue
    raise RuntimeError(f"Unable to detect WireGuard tunnel IPv4 on {conn.host}")


def build_manifest_arg_parser(description, script_file):
    """Create an ArgumentParser with standard GitOps manifest arguments."""
    defaults = default_repo_paths(script_file)
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--source-manifest",
        default=os.environ.get("SOURCE_GITOPS_MANIFEST") or defaults["source_manifest"],
        help="Path to source cluster GitOps manifest YAML",
    )
    parser.add_argument(
        "--target-manifest",
        default=os.environ.get("TARGET_GITOPS_MANIFEST") or defaults["target_manifest"],
        help="Path to target cluster GitOps manifest YAML",
    )
    parser.add_argument(
        "--tables-manifest",
        default=os.environ.get("TABLES_GITOPS_MANIFEST") or defaults["tables_manifest"],
        help="Path to accounts-migration-bootstrap operations YAML declaring tables",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON output in addition to or instead of table output",
    )
    return parser
