#!/usr/bin/env bash
# Run the same reviewed Playbook locally on the isolated recovery host.
set -euo pipefail
umask 077
[[ $EUID == 0 && $# == 5 ]] || exit 2
playbooks_sha="$1" gitops_tag="$2" gitops_sha="$3" phase="$4" inputs="$5"
[[ "$playbooks_sha" =~ ^[0-9a-f]{40}$ && "$gitops_sha" =~ ^[0-9a-f]{40}$ ]] || exit 2
[[ "$gitops_tag" =~ ^prod-v[1-9][0-9]*-recovery-[A-Za-z0-9._-]+$ ]] || exit 2
[[ "$phase" =~ ^(prepare|restore|deploy|verify|preflight)$ ]] || exit 2
[[ "$(hostname -s)" =~ ^web-saas-prod-v[1-9][0-9]*$ ]] || exit 2
[[ "$inputs" =~ ^/home/[a-zA-Z0-9_]+/v3-recovery-inputs$ ]] || exit 2
test -f "$inputs/inventory.json"
test -f "$inputs/web-saas-v3-local-runtime-20261018.yaml"
if ! command -v ansible-playbook >/dev/null; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq ansible
fi
if [[ ! -d /opt/recovery-playbooks/.git ]]; then
  git clone --no-checkout --depth 1 --branch codex/prod-v3-data-volume-20261018 \
    https://github.com/ai-workspace-infra/playbooks.git /opt/recovery-playbooks
fi
git -C /opt/recovery-playbooks fetch --depth 1 origin "$playbooks_sha"
git -C /opt/recovery-playbooks checkout --detach "$playbooks_sha"
git -C /opt/web-saas-recovery-declarations fetch --depth 1 origin "refs/tags/$gitops_tag:refs/tags/$gitops_tag"
git -C /opt/web-saas-recovery-declarations checkout --detach "$gitops_tag"
[[ "$(git -C /opt/web-saas-recovery-declarations rev-parse HEAD)" == "$gitops_sha" ]] || exit 2
AI_WORKSPACE_CMDB_JSON="$inputs/inventory.json" \
  ansible-playbook -i /opt/recovery-playbooks/inventory/terraform_cmdb.py \
    /opt/recovery-playbooks/web-saas-generation-recovery.yml \
    -e "@$inputs/web-saas-v3-local-runtime-20261018.yaml" \
    -e "web_saas_recovery_phase=$phase"
