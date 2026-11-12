#!/usr/bin/env bash
set -euo pipefail

: "${MATRIX_HOST:?MATRIX_HOST must be set (pass matrix.host via step env)}"

cmdb_file="../cmdb/cmdb.json"
mapfile -t node_groups < <(jq -r --arg host "${MATRIX_HOST}" '.[$host].groups[]? // empty' "${cmdb_file}")

# Explicit runtime takes precedence over business groups: a Kubernetes SaaS node
# also belongs to web_saas, which must not send it to a Compose installer.
deployment_mode="$(jq -r --arg host "$MATRIX_HOST" '.[$host].host_vars.deployment_mode // .[$host].deployment_mode // ""' "$cmdb_file")"
if [[ "$deployment_mode" == kubernetes ]]; then
  export CMDB_FILE="$cmdb_file"
  exec bash scripts/pipeline/bootstrap-k3s.sh
fi

playbook=""
for group in "${node_groups[@]}"; do
  case "${group}" in
    web_saas)
      playbook=setup-web-saas-domain.yml
      break
      ;;
    ai_workspace)
      playbook=setup-ai-workspace-rootless.yml
      break
      ;;
    agent_proxy)
      playbook=setup-agent-proxy-domain.yml
      break
      ;;
    infra_platform|open_platform)
      playbook=setup-open-platform-domain.yml
      break
      ;;
    k3s|k3s_server|k3s_agent)
      playbook=setup-k3s-node.yaml
      ;;
    k8s|k8s_node|gpu_k8s)
      playbook=setup-k8s-node.yaml
      ;;
  esac
done

if [[ -z "${playbook}" ]]; then
  echo "No bootstrap playbook mapping found for ${MATRIX_HOST}; CMDB groups: ${node_groups[*]:-none}" >&2
  exit 1
fi

# Open Platform is composed of independently deployable service units. Keep
# the complete entry point as the default, while allowing the orchestrator to
# run Vault, IAM, or Observability separately on the same Terraform namespace.
if [[ "${playbook}" == "setup-open-platform-domain.yml" ]]; then
  case "${OPEN_PLATFORM_SERVICE:-all}" in
    all)
      ;;
    vault)
      playbook=deploy_vault_domain.yml
      ;;
    iam)
      playbook=deploy_iam_domain.yml
      ;;
    observability)
      playbook=deploy_observability_domain.yml
      ;;
    *)
      echo "Unsupported OPEN_PLATFORM_SERVICE=${OPEN_PLATFORM_SERVICE}; expected all, vault, iam, or observability" >&2
      exit 1
      ;;
  esac
fi

if [[ "${playbook}" == "setup-agent-proxy-domain.yml" ]]; then
  # Native agent-proxy delivery must happen after Web SaaS is healthy.  The
  # agent generates its Xray configs only after it can register with Accounts;
  # running the full playbook in this generic bootstrap fan-out would race the
  # controller (and public DNS still points at the previous replica).
  echo "Deferring native agent-proxy bootstrap for ${MATRIX_HOST} until deploy_agent_proxy after Web SaaS observation."
  exit 0
fi

# The bootstrap script can add playbook-specific extra vars without changing
# the common command path.
extra_args=()

# UAT open-platform is a new host whose public Vault DNS is deliberately not
# cut over during bootstrap. Keep VAULT_ADDR public for Vault KV/database
# credential reads, but make the Vault role's service health and CLI calls use
# the local listener until the migration cutover.
if [[ "${playbook}" == "setup-open-platform-domain.yml" || "${playbook}" == "deploy_vault_domain.yml" ]] && [[ -n "${OPEN_PLATFORM_LOCAL_VAULT_ADDR:-}" ]]; then
  extra_args+=( -e "vault_admin_addr=${OPEN_PLATFORM_LOCAL_VAULT_ADDR}" )
fi


echo "Bootstrapping ${MATRIX_HOST} with ${playbook}"
if [[ "${playbook}" == "setup-agent-proxy-domain.yml" ]]; then
  # The agent-proxy domain is native systemd, and its generated CMDB group is
  # agent_proxy. Build the exact requested repository tag on the host; a
  # daily-build tag is not a GitHub Release v* tag and cannot use the release
  # binary download path.
  extra_args+=(
    -e agent_service_hosts=agent_proxy
    -e xray_exporter_hosts=agent_proxy
    -e agent_svc_plus_manage_source_checkout=true
    -e agent_svc_plus_build_on_target=true
  )
fi
ansible-playbook -i ../cmdb/inventory.ini -l "${MATRIX_HOST}" "${playbook}" "${extra_args[@]}"
