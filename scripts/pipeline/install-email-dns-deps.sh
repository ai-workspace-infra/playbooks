#!/bin/bash
# Ansible 依赖，供 configure-email-dns.yaml 使用。
#
# hvac 与 community.hashi_vault 是 playbook 里那个 vault_kv2_get 任务需要的。
# 该任务在 VAULT_TOKEN 为空时会被 when 跳过, 但 Ansible 仍要能解析出模块本身,
# 否则 playbook 在加载阶段就报 "couldn't resolve module/action" —— 跳过与不存在
# 不是一回事。
#
# ansible-core 钉版本, 和本仓库其他流水线保持一致: reconciler 用的是 zip /
# map('list') / unique / subelements 这几个 core filter, 让它们跟着 runner
# 上游走没有好处, 只会让某天的一次上游变更把 DNS 对账弄挂。
set -euo pipefail

ANSIBLE_CORE_VERSION="${ANSIBLE_CORE_VERSION:-2.17.7}"

python3 -m pip install --user --quiet \
  "ansible-core==${ANSIBLE_CORE_VERSION}" \
  hvac \
  jmespath

user_bin="$(python3 -m site --user-base)/bin"
echo "${user_bin}" >> "${GITHUB_PATH}"
export PATH="${user_bin}:${PATH}"

ansible-galaxy collection install community.hashi_vault

ansible --version | head -1
