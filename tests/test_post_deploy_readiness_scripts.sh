#!/usr/bin/env bash
set -euo pipefail

repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
tmp="$(mktemp -d)"
trap 'rm -rf -- "${tmp}"' EXIT
mkdir -p "${tmp}/bin" "${tmp}/configs"
printf '{}\n' > "${tmp}/configs/config.json"
printf '{}\n' > "${tmp}/configs/tcp-config.json"

cat > "${tmp}/bin/docker" <<'MOCK_DOCKER'
#!/usr/bin/env bash
case "$*" in
  *'80/tcp'*) echo 80 ;;
  *'443/tcp'*) echo 443 ;;
  *) echo running ;;
esac
MOCK_DOCKER
chmod +x "${tmp}/bin/docker"
PATH="${tmp}/bin:${PATH}" WEB_SAAS_CONTAINER_READY_TIMEOUT_SECONDS=1 WEB_SAAS_CONTAINER_READY_POLL_SECONDS=0 \
  bash "${repo}/roles/vhosts/web_saas_post_deploy_readiness/files/verify_containers.sh"

cat > "${tmp}/bin/systemctl" <<'MOCK_SYSTEMCTL'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${SYSTEMCTL_CALLS}"
if [[ "$1" == is-active ]]; then echo active; fi
MOCK_SYSTEMCTL
chmod +x "${tmp}/bin/systemctl"
export PATH="${tmp}/bin:${PATH}"
export SYSTEMCTL_CALLS="${tmp}/systemctl-calls"
export AGENT_PROXY_RUNTIME_CONFIG_FILES="${tmp}/configs/config.json ${tmp}/configs/tcp-config.json"
bash "${repo}/roles/vhosts/agent_proxy_post_dns_readiness/files/check_services.sh" 1 0
grep -Fq 'ansible.builtin.systemd_service:' "${repo}/roles/vhosts/agent_proxy_post_dns_readiness/tasks/main.yml"
grep -Fq 'xray.service' "${repo}/roles/vhosts/agent_proxy_post_dns_readiness/tasks/main.yml"
grep -Fq 'xray-tcp.service' "${repo}/roles/vhosts/agent_proxy_post_dns_readiness/tasks/main.yml"
echo 'post-deploy-readiness-script-tests: PASS'
