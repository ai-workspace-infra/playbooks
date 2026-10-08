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
import sys

CMDB_SCHEMA_VERSION = "cmdb.v1"

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


def _host_records(cmdb):
    """Return canonical host records from cmdb.v1 or legacy CMDB shapes."""
    if not isinstance(cmdb, dict):
        raise ValueError("CMDB root must be an object")

    schema_version = cmdb.get("schema_version")
    if schema_version and schema_version != CMDB_SCHEMA_VERSION:
        raise ValueError(f"unsupported CMDB schema_version: {schema_version}")

    hosts = cmdb.get("hosts")
    if isinstance(hosts, dict):
        records = dict(hosts)
    else:
        # Older AWS/Vultr CMDBs and existing selfhost receipts keep host
        # records at the top level alongside environment metadata.
        records = {
            name: value
            for name, value in cmdb.items()
            if isinstance(value, dict) and ("ip" in value or "ansible_host" in value)
        }

    # Older GCP CMDBs kept Vault nodes in a list rather than as inventory
    # records. Accept that shape while new IaC output is rolled out.
    for node in cmdb.get("vault_nodes", []) or []:
        if not isinstance(node, dict) or not node.get("name"):
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
        hostvars = {
            "ansible_host": address,
            "ansible_user": host.get("ansible_user", "root"),
            "ansible_port": host.get("ansible_port", 22),
            "cmdb_environment": cmdb.get("environment"),
            "cmdb_cloud_provider": host.get("cloud_provider")
            or cmdb.get("cloud_provider")
            or host.get("provider"),
            "cmdb_project_id": host.get("project_id") or cmdb.get("project_id"),
            "cmdb_resource_id": host.get("resource_id") or host.get("instance_id"),
            "cmdb_instance_id": host.get("instance_id"),
            "cmdb_os_id": host.get("os_id"),
            "cmdb_tags": host.get("tags", []),
            "cmdb_source_resources": cmdb.get("source_resources", []),
        }
        # CMDB 其余字段一并暴露给 playbook 使用
        hostvars.update(host.get("host_vars", {}))
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
