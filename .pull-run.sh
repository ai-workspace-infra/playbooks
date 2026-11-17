#!/bin/bash
set +e
D=/var/lib/ansible-pull/playbooks-observability/ansible-pull-observability
rm -rf "$D"; mkdir -p "$(dirname "$D")"
git clone --depth 1 -b feat/pull-auth-preserving https://github.com/ai-workspace-infra/playbooks.git "$D" -q 2>&1|tail -2
echo "HEAD: $(git -C "$D" rev-parse --short HEAD)"

echo
echo "=== 记录运行前 vector.toml 的 auth 指纹 ==="
awk '/^\[sinks\.prometheus_remote\.auth\]/{f=1;next} /^\[/{f=0} f' /etc/vector/vector.toml | md5sum | awk '{print "auth-block-md5="$1}'

cd "$D"
ANSIBLE_ROLES_PATH="$D/roles" ANSIBLE_HOST_KEY_CHECKING=False \
ansible-playbook -i 'localhost,' -c local pull_observability_agent.yml \
  -e "xconnect_gitops_config=/var/lib/ansible-pull/gitops/topology/prod/selfhost/xconnect-regional-pools.yaml" \
  > /tmp/pull-run.log 2>&1
echo "ansible rc=$?"
echo
echo "=== RECAP ==="
sed -n '/PLAY RECAP/,/^$/p' /tmp/pull-run.log | head -6
echo
echo "=== 失败详情(若有) ==="
grep -E "fatal:|FAILED!" -A4 /tmp/pull-run.log | head -40
