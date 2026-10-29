#!/usr/bin/env python3
"""Ansible 动态 inventory —— 数据源为 Terraform 导出的 CMDB。

与 IAC 联动方式：
  iac_modules/terraform-hcl-standard/vultr-vps/envs/ai-workspace/ 的 generate.py
  在 `terraform apply` 后，把 YAML 静态字段与 terraform 运行时输出合并写出
  cmdb.json（结构化主机事实）。本脚本把它翻译成 Ansible 动态 inventory，
  于是 IaC 一变更、重跑 `generate.py inventory`，inventory 就跟着变。

取数优先级：
  1. 环境变量 AI_WORKSPACE_CMDB_JSON 指向的文件
  2. 环境变量 AI_WORKSPACE_TF_DIR（或默认 env 目录）下的 cmdb.json

用法：
  ansible-inventory -i inventory/terraform_cmdb.py --list
  ansible all -i inventory/terraform_cmdb.py -m ping
"""

import json
import os
import re
import sys

CMDB_SCHEMA_VERSION = "cmdb.v1"

SERVERLESS_RESOURCE_KINDS = {
    "serverless",
    "serverless_service",
    "cloudrun",
    "cloud_run",
    "cloud_run_service",
    "worker",
    "workers",
    "cloudflare_worker",
    "pages",
    "cloudflare_pages",
    "supabase",
    "managed_database",
}

K3S_MARKERS = {"k3s", "k3s_server", "k3s_agent", "kubernetes"}
DOCKER_GROUPS = {
    "docker",
    "docker_hosts",
    "compose",
    "docker-compose",
    "container",
}
ZONE_SUFFIX_RE = re.compile(r"^([a-z]+-[a-z]+[0-9]+)-[a-z]$")
GCP_ZONE_IN_ID_RE = re.compile(r"/zones/([^/]+)/")
GCP_REGION_IN_ID_RE = re.compile(r"/regions/([^/]+)/")
GCP_PROJECT_IN_ID_RE = re.compile(r"^projects/([^/]+)/")

HERE = os.path.dirname(os.path.abspath(__file__))
# playbooks/inventory -> 仓库根 -> terraform env
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
DEFAULT_TF_DIR = os.path.join(
    REPO_ROOT,
    "iac_modules",
    "terraform-hcl-standard",
    "vultr-vps",
    "envs",
    "ai-workspace",
)


def _from_explicit_file():
    path = os.environ.get("AI_WORKSPACE_CMDB_JSON") or os.environ.get("CMDB_FILE")
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return None


def _from_default_file(tf_dir):
    path = os.path.join(tf_dir, "cmdb.json")
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return None


def load_cmdb():
    tf_dir = os.environ.get("AI_WORKSPACE_TF_DIR", DEFAULT_TF_DIR)
    for loader in (
        _from_explicit_file,
        lambda: _from_default_file(tf_dir),
    ):
        data = loader()
        if data:
            return data
    return {}


def _is_serverless_record(host):
    """Return True when a CMDB record describes a non-SSH Serverless resource."""
    if not isinstance(host, dict):
        return False
    host_vars = host.get("host_vars") if isinstance(host.get("host_vars"), dict) else {}

    if host.get("serverless") is True or host_vars.get("serverless") is True:
        return True
    if host.get("ssh_enabled") is False or host_vars.get("ssh_enabled") is False:
        return True

    for field in ("runtime_form", "deployment_mode", "mode"):
        val = str(host.get(field) or host_vars.get(field) or "").strip().lower()
        if val == "serverless":
            return True

    for field in ("resource_kind", "resource_type", "kind", "type"):
        val = str(host.get(field) or host_vars.get(field) or "").strip().lower()
        if val in SERVERLESS_RESOURCE_KINDS:
            return True

    groups = {str(g).strip().lower() for g in (host.get("groups") or []) if g}
    tags = {str(t).strip().lower() for t in (host.get("tags") or []) if t}
    if "serverless" in groups or "serverless" in tags or bool(groups & SERVERLESS_RESOURCE_KINDS) or bool(tags & SERVERLESS_RESOURCE_KINDS):
        return True

    provider = str(
        host.get("cloud_provider")
        or host.get("provider")
        or host_vars.get("cloud_provider")
        or host_vars.get("provider")
        or ""
    ).strip().lower()
    address = str(host.get("ip") or host.get("ansible_host") or "").strip()
    if provider in {"cloudflare", "supabase"} and not address:
        return True

    return False


def _resolve_region(host, cmdb):
    """Resolve canonical region from host, host_vars, zone, resource_id, or CMDB root."""
    host_vars = host.get("host_vars") if isinstance(host.get("host_vars"), dict) else {}
    for candidate in (
        host.get("region"),
        host.get("cloud_region"),
        host.get("provider_region"),
        host.get("logical_region"),
        host_vars.get("region"),
        host_vars.get("cloud_region"),
        host_vars.get("provider_region"),
        host_vars.get("logical_region"),
        cmdb.get("region"),
    ):
        if candidate and str(candidate).strip():
            return str(candidate).strip()

    zone = str(host.get("zone") or host_vars.get("zone") or cmdb.get("zone") or "").strip()
    if zone:
        match = ZONE_SUFFIX_RE.match(zone)
        return match.group(1) if match else zone

    resource_id = str(host.get("resource_id") or host.get("instance_id") or "").strip()
    if resource_id:
        region_match = GCP_REGION_IN_ID_RE.search(resource_id)
        if region_match:
            return region_match.group(1)
        zone_match = GCP_ZONE_IN_ID_RE.search(resource_id)
        if zone_match:
            z = zone_match.group(1)
            m = ZONE_SUFFIX_RE.match(z)
            return m.group(1) if m else z

    return "unknown"


def _resolve_runtime_form(host):
    """Classify host runtime form as systemd, docker, or k3s."""
    host_vars = host.get("host_vars") if isinstance(host.get("host_vars"), dict) else {}
    explicit = str(host.get("runtime_form") or host_vars.get("runtime_form") or "").strip().lower()
    if explicit in {"systemd", "docker", "k3s"}:
        return explicit

    deployment_mode = str(
        host.get("deployment_mode") or host_vars.get("deployment_mode") or ""
    ).strip().lower()
    service_variant = str(
        host.get("service_variant") or host_vars.get("service_variant") or ""
    ).strip().lower()
    groups = {str(g).strip().lower() for g in (host.get("groups") or []) if g}
    tags = {str(t).strip().lower() for t in (host.get("tags") or []) if t}

    if (
        deployment_mode in {"kubernetes", "k3s"}
        or service_variant.startswith("k3s")
        or bool(groups & K3S_MARKERS)
        or bool(tags & K3S_MARKERS)
        or any(g.startswith("k3s") for g in groups | tags)
    ):
        return "k3s"

    if (
        deployment_mode in {"docker", "compose", "docker-compose", "selfhost", "container"}
        or "doco_cd_image" in host_vars
        or bool(groups & DOCKER_GROUPS)
        or "docker" in tags
    ):
        return "docker"

    return "systemd"


def _host_records(cmdb):
    """Return canonical host records from cmdb.v1 or legacy CMDB shapes."""
    if not isinstance(cmdb, dict):
        raise ValueError("CMDB root must be an object")

    schema_version = cmdb.get("schema_version")
    if schema_version and schema_version != CMDB_SCHEMA_VERSION:
        raise ValueError(f"unsupported CMDB schema_version: {schema_version}")

    hosts = cmdb.get("hosts")
    if isinstance(hosts, dict):
        records = {
            name: value
            for name, value in hosts.items()
            if isinstance(value, dict) and not _is_serverless_record(value)
        }
    else:
        # Older AWS/Vultr CMDBs and existing selfhost receipts keep host
        # records at the top level alongside environment metadata.
        records = {
            name: value
            for name, value in cmdb.items()
            if isinstance(value, dict)
            and ("ip" in value or "ansible_host" in value)
            and not _is_serverless_record(value)
        }

    # Older GCP CMDBs kept Vault nodes in a list rather than as inventory
    # records. Accept that shape while new IaC output is rolled out.
    for node in cmdb.get("vault_nodes", []) or []:
        if not isinstance(node, dict) or not node.get("name") or _is_serverless_record(node):
            continue
        records.setdefault(
            node["name"],
            {
                **node,
                "ip": node.get("ip") or node.get("ansible_host"),
                "groups": node.get("groups") or ["vault"],
            },
        )
    return records


def build_inventory(cmdb):
    inv = {"_meta": {"hostvars": {}}}
    groups = {}

    for name, host in _host_records(cmdb).items():
        address = host.get("ip") or host.get("ansible_host")
        if not str(address or "").strip():
            raise ValueError(f"CMDB host {name} has no runtime ip/ansible_host")

        host_vars = host.get("host_vars") if isinstance(host.get("host_vars"), dict) else {}
        environment = (
            host.get("environment")
            or host_vars.get("environment")
            or cmdb.get("environment")
            or "unknown"
        )
        provider = (
            host.get("cloud_provider")
            or cmdb.get("cloud_provider")
            or host.get("provider")
            or host_vars.get("cloud_provider")
            or host_vars.get("provider")
            or "unknown"
        )
        resource_id = (
            host.get("resource_id")
            or host.get("instance_id")
            or host_vars.get("resource_id")
            or host_vars.get("instance_id")
            or host.get("name")
            or name
        )
        project_id = (
            host.get("project_id")
            or cmdb.get("project_id")
            or host.get("account")
            or cmdb.get("account")
            or host_vars.get("project_id")
            or host_vars.get("account")
        )
        if not project_id and isinstance(resource_id, str):
            proj_match = GCP_PROJECT_IN_ID_RE.search(resource_id)
            if proj_match:
                project_id = proj_match.group(1)

        region = _resolve_region(host, cmdb)
        runtime_form = _resolve_runtime_form(host)
        account_or_project = project_id or "unknown"
        resource_key = (
            host.get("resource_key")
            or host_vars.get("resource_key")
            or f"{provider}:{account_or_project}:{region}:{resource_id}"
        )

        hostvars = {
            "cmdb_name": host.get("name") or name,
            "ansible_host": address,
            "ansible_user": host.get("ansible_user", "root"),
            "ansible_port": host.get("ansible_port", 22),
            "cmdb_environment": cmdb.get("environment") or host.get("environment") or host_vars.get("environment"),
            "environment": environment,
            "cmdb_cloud_provider": provider if provider != "unknown" else None,
            "cloud_provider": provider,
            "provider": provider,
            "cmdb_project_id": project_id,
            "project_id": project_id,
            "cmdb_region": region,
            "region": region,
            "cmdb_resource_id": host.get("resource_id") or host.get("instance_id") or resource_id,
            "resource_id": resource_id,
            "cmdb_instance_id": host.get("instance_id"),
            "cmdb_runtime_form": runtime_form,
            "runtime_form": runtime_form,
            "cmdb_resource_key": resource_key,
            "resource_key": resource_key,
            "cmdb_os_id": host.get("os_id"),
            "cmdb_tags": host.get("tags", []),
            "cmdb_source_resources": cmdb.get("source_resources", []),
        }
        if host.get("ssh_private_key_file"):
            hostvars["ansible_ssh_private_key_file"] = host["ssh_private_key_file"]
        # CMDB 其余字段一并暴露给 playbook 使用
        hostvars.update(host_vars)
        if isinstance(host.get("data_volume"), dict):
            hostvars["data_volume"] = host["data_volume"]
        inv["_meta"]["hostvars"][name] = hostvars

        for group in host.get("groups", []) or ["ungrouped"]:
            if not isinstance(group, str) or not group:
                continue
            groups.setdefault(group, {"hosts": []})["hosts"].append(name)

    inv.update(groups)
    inv["all"] = {"children": sorted(list(groups.keys()) + ["ungrouped"])}
    return inv


def main():
    args = sys.argv[1:]
    cmdb = load_cmdb()

    if "--host" in args:
        host_name = args[args.index("--host") + 1] if args.index("--host") + 1 < len(args) else ""
        print(json.dumps(build_inventory(cmdb)["_meta"]["hostvars"].get(host_name, {})))
        return

    # 默认与 --list 行为一致
    print(json.dumps(build_inventory(cmdb), indent=2))


if __name__ == "__main__":
    main()
