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
    path = os.environ.get("AI_WORKSPACE_CMDB_JSON")
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


def build_inventory(cmdb):
    inv = {"_meta": {"hostvars": {}}}
    groups = {}

    for name, host in cmdb.items():
        hostvars = {
            "ansible_host": host.get("ip"),
            "ansible_user": host.get("ansible_user", "root"),
            # 云主机 IP 常被回收，放宽 host key 校验避免撞到旧 known_hosts
            "ansible_ssh_common_args": (
                "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
            ),
        }
        # CMDB 其余字段一并暴露给 playbook 使用
        hostvars.update(host.get("host_vars", {}))
        hostvars["cmdb_instance_id"] = host.get("instance_id")
        hostvars["cmdb_os_id"] = host.get("os_id")
        hostvars["cmdb_tags"] = host.get("tags", [])
        inv["_meta"]["hostvars"][name] = hostvars

        for group in host.get("groups", []) or ["ungrouped"]:
            groups.setdefault(group, {"hosts": []})["hosts"].append(name)

    inv.update(groups)
    inv["all"] = {"children": sorted(list(groups.keys()) + ["ungrouped"])}
    return inv


def main():
    args = sys.argv[1:]
    cmdb = load_cmdb()

    if "--host" in args:
        # hostvars 已在 _meta 里，单主机查询返回空对象即可
        print(json.dumps({}))
        return

    # 默认与 --list 行为一致
    print(json.dumps(build_inventory(cmdb), indent=2))


if __name__ == "__main__":
    main()
