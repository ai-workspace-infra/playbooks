#!/bin/bash
set +e
D=/var/lib/ansible-pull/playbooks-observability/ansible-pull-observability
rm -rf "$D"; mkdir -p "$(dirname "$D")"
git clone --depth 1 -b feat/pull-auth-preserving https://github.com/ai-workspace-infra/playbooks.git "$D" -q 2>&1|tail -2
echo "HEAD: $(git -C "$D" rev-parse --short HEAD)"
cd "$D"
ANSIBLE_ROLES_PATH="$D/roles" ANSIBLE_HOST_KEY_CHECKING=False \
ansible-playbook -i 'localhost,' -c local pull_observability_agent.yml \
  -e "xconnect_gitops_config=/var/lib/ansible-pull/gitops/topology/prod/selfhost/xconnect-regional-pools.yml" \
  > /tmp/pull-run.log 2>&1
echo "ansible rc=$?"
sed -n '/PLAY RECAP/,/^$/p' /tmp/pull-run.log | head -4
echo "=== 失败详情 ==="
grep -E "fatal:|FAILED!" -A4 /tmp/pull-run.log | head -30
