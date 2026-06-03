#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLOUD_NEUTRAL_DIR="$(cd "${ROOT_DIR}/.." && pwd)"
ACCOUNTS_REPO="${ACCOUNTS_REPO:-${CLOUD_NEUTRAL_DIR}/accounts.svc.plus}"
ACCOUNTS_SERVICE_URL="${ACCOUNTS_SERVICE_URL:-https://accounts.svc.plus}"
OVERLAY_NODE_ID="${OVERLAY_NODE_ID:-xworkmate-bridge}"
OVERLAY_GROUP_VARS="${OVERLAY_GROUP_VARS:-${ROOT_DIR}/group_vars/xworkmate_bridge_distributed.yml}"
OVERLAY_ATTACH_TO="${OVERLAY_ATTACH_TO:-jp-xhttp-contabo.svc.plus,cn-xworkmate-bridge.svc.plus}"
OVERLAY_REGISTER_ARGS="${OVERLAY_REGISTER_ARGS:-}"
OVERLAY_USE_SUDO="${OVERLAY_USE_SUDO:-1}"
OVERLAY_SKIP_DEPLOY="${OVERLAY_SKIP_DEPLOY:-0}"
OVERLAY_SKIP_UP="${OVERLAY_SKIP_UP:-0}"
OVERLAY_TEARDOWN="${OVERLAY_TEARDOWN:-0}"
OVERLAY_TEARDOWN_ON_ERROR="${OVERLAY_TEARDOWN_ON_ERROR:-0}"
OVERLAY_STATE_FILE="${OVERLAY_STATE_FILE:-${HOME}/.xoverlay/session.json}"
OVERLAY_SKIP_LOCAL_TOOL_CHECK="${OVERLAY_SKIP_LOCAL_TOOL_CHECK:-0}"
OVERLAY_ANSIBLE_SYNTAX_ARGS="${OVERLAY_ANSIBLE_SYNTAX_ARGS:-}"
OVERLAY_ANSIBLE_DEPLOY_ARGS="${OVERLAY_ANSIBLE_DEPLOY_ARGS:--f 1}"
OVERLAY_CONFIG_FILE="${OVERLAY_CONFIG_FILE:-${HOME}/.xoverlay/overlay-config.json}"
OVERLAY_EVIDENCE_DIR="${OVERLAY_EVIDENCE_DIR:-/tmp/wireguard-over-vless-closure-$(date -u +%Y%m%dT%H%M%SZ)}"
OVERLAY_CAPTURE_LOG="${OVERLAY_CAPTURE_LOG:-1}"
OVERLAY_CHECK_ONLY="${OVERLAY_CHECK_ONLY:-0}"
OVERLAY_BUILD_BIN="${OVERLAY_BUILD_BIN:-0}"
OVERLAY_BUILD_BIN_PATH="${OVERLAY_BUILD_BIN_PATH:-${OVERLAY_EVIDENCE_DIR}/overlayctl}"
OVERLAY_ENV_FILES="${OVERLAY_ENV_FILES:-}"
overlay_runtime_started=0
missing_required_envs=()
missing_required_tools=()
missing_required_paths=()
loaded_env_keys=()
loaded_env_files=()
active_step=""
if [[ "${OVERLAY_CAPTURE_LOG}" == "1" ]]; then
  mkdir -p "${OVERLAY_EVIDENCE_DIR}"
  run_log_fifo="${OVERLAY_EVIDENCE_DIR}/.run-log.fifo"
  rm -f "${run_log_fifo}"
  mkfifo "${run_log_fifo}"
  tee -a "${OVERLAY_EVIDENCE_DIR}/run.log" < "${run_log_fifo}" &
  run_log_tee_pid=$!
  exec 3>&1 4>&2
  exec > "${run_log_fifo}" 2>&1
  rm -f "${run_log_fifo}"
fi

sha256_file() {
  local path="$1"
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "${path}" | awk '{print $1}'
    return
  fi
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "${path}" | awk '{print $1}'
    return
  fi
  return 1
}

git_head() {
  local repo="$1"
  git -C "${repo}" rev-parse HEAD 2>/dev/null || true
}

git_dirty() {
  local repo="$1"
  if ! git -C "${repo}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "unknown"
    return
  fi
  if [[ -n "$(git -C "${repo}" status --short 2>/dev/null)" ]]; then
    echo "1"
    return
  fi
  echo "0"
}

is_overlay_env_key() {
  case "$1" in
    ACCOUNT_EMAIL|ACCOUNT_PASSWORD|BRIDGE_AUTH_TOKEN|VAULT_SERVER_ROOT_ACCESS_TOKEN|VAULT_TOKEN|INTERNAL_SERVICE_TOKEN)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

trim_quotes() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  if [[ "${value}" == \"*\" && "${value}" == *\" ]]; then
    value="${value:1:${#value}-2}"
  elif [[ "${value}" == \'*\' && "${value}" == *\' ]]; then
    value="${value:1:${#value}-2}"
  fi
  printf '%s' "${value}"
}

shell_quote() {
  printf "%q" "$1"
}

mark_step() {
  local step="$1"
  local status="$2"
  mkdir -p "${OVERLAY_EVIDENCE_DIR}"
  printf '%s\t%s\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${step}" "${status}" >> "${OVERLAY_EVIDENCE_DIR}/steps.log"
}

begin_step() {
  active_step="$1"
}

complete_step() {
  local step="$1"
  local status="$2"
  mark_step "${step}" "${status}"
  if [[ "${active_step}" == "${step}" ]]; then
    active_step=""
  fi
}

step_status() {
  local step="$1"
  if [[ ! -f "${OVERLAY_EVIDENCE_DIR}/steps.log" ]]; then
    echo "not_run"
    return
  fi
  awk -F '\t' -v step="${step}" '$2 == step { status = $3 } END { if (status == "") { print "not_run" } else { print status } }' "${OVERLAY_EVIDENCE_DIR}/steps.log"
}

write_closure_requirements() {
  local output="$1"
  local account_login_status="$2"
  local device_register_status="$3"
  local config_initial_status="$4"
  local playbooks_projection_status="$5"
  local gateway_deploy_status="$6"
  local config_refresh_status="$7"
  local local_runtime_status="$8"
  local connectivity_status="$9"
  local ack_status="${10}"
  {
    printf 'requirement\tstep\tstatus\trequired_for_completion\n'
    printf 'account_login\t01.login\t%s\t1\n' "${account_login_status}"
    printf 'device_registration\t02.register_device\t%s\t1\n' "${device_register_status}"
    printf 'initial_config_sync_render_preflight\t03.initial_sync_render_preflight\t%s\t1\n' "${config_initial_status}"
    printf 'playbooks_client_projection\t04.apply_playbooks_client\t%s\t1\n' "${playbooks_projection_status}"
    printf 'gateway_deploy_and_heartbeat\t05.deploy_gateway\t%s\t1\n' "${gateway_deploy_status}"
    printf 'post_heartbeat_config_refresh\t06.refresh_sync_render_preflight\t%s\t1\n' "${config_refresh_status}"
    printf 'local_runtime_up\t07.local_runtime_up\t%s\t1\n' "${local_runtime_status}"
    printf 'private_connectivity\t08.connectivity\t%s\t1\n' "${connectivity_status}"
    printf 'config_ack\t09.ack_config\t%s\t1\n' "${ack_status}"
    printf 'optional_teardown\t10.teardown\t%s\t0\n' "$(step_status "10.teardown")"
  } > "${output}"
}

closure_complete_value() {
  local account_login_status="$1"
  local device_register_status="$2"
  local config_initial_status="$3"
  local playbooks_projection_status="$4"
  local gateway_deploy_status="$5"
  local config_refresh_status="$6"
  local local_runtime_status="$7"
  local connectivity_status="$8"
  local ack_status="$9"
  if [[ "${account_login_status}" == "ok" \
    && "${device_register_status}" == "ok" \
    && "${config_initial_status}" == "ok" \
    && "${playbooks_projection_status}" == "ok" \
    && "${gateway_deploy_status}" == "ok" \
    && "${config_refresh_status}" == "ok" \
    && "${local_runtime_status}" == "ok" \
    && "${connectivity_status}" == "ok" \
    && "${ack_status}" == "ok" ]]; then
    echo "1"
    return
  fi
  echo "0"
}

write_closure_verdict() {
  local output="$1"
  local complete="$2"
  local failed_items
  failed_items="$(awk -F '\t' 'NR > 1 && $4 == "1" && $3 != "ok" { items = items ? items "," $1 ":" $3 : $1 ":" $3 } END { print items }' "${OVERLAY_EVIDENCE_DIR}/closure-requirements.tsv")"
  {
    echo "closure_ready=${complete}"
    echo "required_items_failed=${failed_items}"
    echo "requirements_file=${OVERLAY_EVIDENCE_DIR}/closure-requirements.tsv"
  } > "${output}"
}

load_env_files() {
  if [[ -z "${OVERLAY_ENV_FILES}" ]]; then
    return
  fi
  local env_file line key value
  IFS=',' read -r -a env_files <<< "${OVERLAY_ENV_FILES}"
  for env_file in "${env_files[@]}"; do
    env_file="${env_file#"${env_file%%[![:space:]]*}"}"
    env_file="${env_file%"${env_file##*[![:space:]]}"}"
    if [[ -z "${env_file}" ]]; then
      continue
    fi
    if [[ ! -f "${env_file}" ]]; then
      missing_required_paths+=("overlay_env_file:${env_file}")
      continue
    fi
    loaded_env_files+=("${env_file}")
    while IFS= read -r line || [[ -n "${line}" ]]; do
      line="${line#"${line%%[![:space:]]*}"}"
      [[ -z "${line}" || "${line}" == \#* ]] && continue
      [[ "${line}" == export\ * ]] && line="${line#export }"
      [[ "${line}" != *=* ]] && continue
      key="${line%%=*}"
      value="${line#*=}"
      key="${key#"${key%%[![:space:]]*}"}"
      key="${key%"${key##*[![:space:]]}"}"
      if [[ "${key}" == "${line}" || -z "${key}" ]]; then
        continue
      fi
      if ! is_overlay_env_key "${key}"; then
        continue
      fi
      if [[ -n "${!key:-}" ]]; then
        continue
      fi
      value="$(trim_quotes "${value}")"
      if [[ -z "${value}" ]]; then
        continue
      fi
      export "${key}=${value}"
      loaded_env_keys+=("${key}@${env_file}")
    done < "${env_file}"
  done
}

write_evidence() {
  local status="$1"
  mkdir -p "${OVERLAY_EVIDENCE_DIR}"
  {
    echo "status=${status}"
    echo "timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "accounts_repo=${ACCOUNTS_REPO}"
    echo "accounts_git_head=$(git_head "${ACCOUNTS_REPO}")"
    echo "accounts_git_dirty=$(git_dirty "${ACCOUNTS_REPO}")"
    echo "playbooks_git_head=$(git_head "${ROOT_DIR}")"
    echo "playbooks_git_dirty=$(git_dirty "${ROOT_DIR}")"
    echo "accounts_service_url=${ACCOUNTS_SERVICE_URL}"
    echo "overlay_node_id=${OVERLAY_NODE_ID}"
    echo "overlay_group_vars=${OVERLAY_GROUP_VARS}"
    echo "overlay_attach_to=${OVERLAY_ATTACH_TO}"
    echo "overlay_skip_deploy=${OVERLAY_SKIP_DEPLOY}"
    echo "overlay_skip_up=${OVERLAY_SKIP_UP}"
    echo "overlay_use_sudo=${OVERLAY_USE_SUDO}"
    echo "overlay_teardown=${OVERLAY_TEARDOWN}"
    echo "overlay_teardown_on_error=${OVERLAY_TEARDOWN_ON_ERROR}"
    echo "overlay_state_file=${OVERLAY_STATE_FILE}"
    echo "overlay_config_file=${OVERLAY_CONFIG_FILE}"
    echo "overlay_check_only=${OVERLAY_CHECK_ONLY}"
    echo "overlay_build_bin=${OVERLAY_BUILD_BIN}"
    echo "overlay_build_bin_path=${OVERLAY_BUILD_BIN_PATH}"
    echo "overlay_env_files=${OVERLAY_ENV_FILES}"
    echo "overlayctl_bin=${OVERLAYCTL_BIN:-}"
    local account_login_status device_register_status config_initial_status playbooks_projection_status
    local gateway_deploy_status config_refresh_status local_runtime_status connectivity_status ack_status
    account_login_status="$(step_status "01.login")"
    device_register_status="$(step_status "02.register_device")"
    config_initial_status="$(step_status "03.initial_sync_render_preflight")"
    playbooks_projection_status="$(step_status "04.apply_playbooks_client")"
    gateway_deploy_status="$(step_status "05.deploy_gateway")"
    config_refresh_status="$(step_status "06.refresh_sync_render_preflight")"
    local_runtime_status="$(step_status "07.local_runtime_up")"
    connectivity_status="$(step_status "08.connectivity")"
    ack_status="$(step_status "09.ack_config")"
    local closure_complete
    closure_complete="$(closure_complete_value \
      "${account_login_status}" \
      "${device_register_status}" \
      "${config_initial_status}" \
      "${playbooks_projection_status}" \
      "${gateway_deploy_status}" \
      "${config_refresh_status}" \
      "${local_runtime_status}" \
      "${connectivity_status}" \
      "${ack_status}")"
    write_closure_requirements \
      "${OVERLAY_EVIDENCE_DIR}/closure-requirements.tsv" \
      "${account_login_status}" \
      "${device_register_status}" \
      "${config_initial_status}" \
      "${playbooks_projection_status}" \
      "${gateway_deploy_status}" \
      "${config_refresh_status}" \
      "${local_runtime_status}" \
      "${connectivity_status}" \
      "${ack_status}"
    write_closure_verdict "${OVERLAY_EVIDENCE_DIR}/closure-verdict.env" "${closure_complete}"
    echo "closure_account_login_status=${account_login_status}"
    echo "closure_device_register_status=${device_register_status}"
    echo "closure_config_initial_status=${config_initial_status}"
    echo "closure_playbooks_projection_status=${playbooks_projection_status}"
    echo "closure_gateway_deploy_status=${gateway_deploy_status}"
    echo "closure_config_refresh_status=${config_refresh_status}"
    echo "closure_local_runtime_status=${local_runtime_status}"
    echo "closure_connectivity_status=${connectivity_status}"
    echo "closure_ack_status=${ack_status}"
    echo "closure_complete=${closure_complete}"
    if [[ -s "${OVERLAY_EVIDENCE_DIR}/steps.log" ]]; then
      local last_step_timestamp last_step last_step_status
      IFS=$'\t' read -r last_step_timestamp last_step last_step_status < <(tail -n 1 "${OVERLAY_EVIDENCE_DIR}/steps.log")
      echo "last_step_timestamp=${last_step_timestamp}"
      echo "last_step=${last_step}"
      echo "last_step_status=${last_step_status}"
    fi
    if [[ ${#loaded_env_files[@]} -gt 0 ]]; then
      local joined_loaded_env_files
      joined_loaded_env_files="$(IFS=','; echo "${loaded_env_files[*]}")"
      echo "loaded_env_files=${joined_loaded_env_files}"
    fi
    if [[ ${#loaded_env_keys[@]} -gt 0 ]]; then
      local joined_loaded_env_keys
      joined_loaded_env_keys="$(IFS=','; echo "${loaded_env_keys[*]}")"
      echo "loaded_env_keys=${joined_loaded_env_keys}"
    fi
    if [[ -n "${OVERLAYCTL_BIN:-}" && -x "${OVERLAYCTL_BIN}" ]]; then
      overlayctl_sha256="$(sha256_file "${OVERLAYCTL_BIN}" 2>/dev/null || true)"
      if [[ -n "${overlayctl_sha256}" ]]; then
        echo "overlayctl_sha256=${overlayctl_sha256}"
      fi
    fi
    if [[ ${#missing_required_envs[@]} -gt 0 ]]; then
      local joined_missing_envs
      joined_missing_envs="$(IFS=','; echo "${missing_required_envs[*]}")"
      echo "missing_required_envs=${joined_missing_envs}"
    fi
    if [[ ${#missing_required_tools[@]} -gt 0 ]]; then
      local joined_missing_tools
      joined_missing_tools="$(IFS=','; echo "${missing_required_tools[*]}")"
      echo "missing_required_tools=${joined_missing_tools}"
    fi
    if [[ ${#missing_required_paths[@]} -gt 0 ]]; then
      local joined_missing_paths
      joined_missing_paths="$(IFS=','; echo "${missing_required_paths[*]}")"
      echo "missing_required_paths=${joined_missing_paths}"
    fi
  } > "${OVERLAY_EVIDENCE_DIR}/summary.env"
  {
    echo "# Non-secret settings for rerunning the closure script."
    echo "# Source or copy these exports, then provide the missing secret values separately."
    echo "export ACCOUNTS_REPO=$(shell_quote "${ACCOUNTS_REPO}")"
    echo "export ACCOUNTS_SERVICE_URL=$(shell_quote "${ACCOUNTS_SERVICE_URL}")"
    echo "export OVERLAY_NODE_ID=$(shell_quote "${OVERLAY_NODE_ID}")"
    echo "export OVERLAY_GROUP_VARS=$(shell_quote "${OVERLAY_GROUP_VARS}")"
    echo "export OVERLAY_ATTACH_TO=$(shell_quote "${OVERLAY_ATTACH_TO}")"
    echo "export OVERLAY_USE_SUDO=$(shell_quote "${OVERLAY_USE_SUDO}")"
    echo "export OVERLAY_SKIP_DEPLOY=$(shell_quote "${OVERLAY_SKIP_DEPLOY}")"
    echo "export OVERLAY_SKIP_UP=$(shell_quote "${OVERLAY_SKIP_UP}")"
    echo "export OVERLAY_TEARDOWN=$(shell_quote "${OVERLAY_TEARDOWN}")"
    echo "export OVERLAY_TEARDOWN_ON_ERROR=$(shell_quote "${OVERLAY_TEARDOWN_ON_ERROR}")"
    echo "export OVERLAY_STATE_FILE=$(shell_quote "${OVERLAY_STATE_FILE}")"
    echo "export OVERLAY_CONFIG_FILE=$(shell_quote "${OVERLAY_CONFIG_FILE}")"
    echo "export OVERLAY_ENV_FILES=$(shell_quote "${OVERLAY_ENV_FILES}")"
    echo "export OVERLAY_BUILD_BIN=$(shell_quote "${OVERLAY_BUILD_BIN}")"
    echo "# OVERLAY_BUILD_BIN_PATH was $(shell_quote "${OVERLAY_BUILD_BIN_PATH}")"
    echo "# Leave it unset to build into the next run's evidence directory."
    if [[ ${#missing_required_envs[@]} -gt 0 ]]; then
      local joined_missing_envs
      joined_missing_envs="$(IFS=','; echo "${missing_required_envs[*]}")"
      echo "# missing_required_envs=${joined_missing_envs}"
    fi
    echo "# Recommended preflight:"
    echo "# OVERLAY_CHECK_ONLY=1 scripts/verify-wireguard-over-vless-closure.sh"
    echo "# Recommended full run:"
    echo "# scripts/verify-wireguard-over-vless-closure.sh"
  } > "${OVERLAY_EVIDENCE_DIR}/rerun.env"
  {
    command -v go >/dev/null 2>&1 && go version 2>/dev/null || true
    command -v ansible-playbook >/dev/null 2>&1 && ansible-playbook --version 2>/dev/null | head -n 1 || true
    command -v wg >/dev/null 2>&1 && wg --version 2>/dev/null || true
    command -v wg-quick >/dev/null 2>&1 && echo "wg-quick $(command -v wg-quick)" || true
    command -v xray >/dev/null 2>&1 && xray version 2>/dev/null | head -n 1 || true
    if [[ -n "${OVERLAYCTL_BIN:-}" ]]; then
      echo "overlayctl ${OVERLAYCTL_BIN}"
      overlayctl_sha256="$(sha256_file "${OVERLAYCTL_BIN}" 2>/dev/null || true)"
      if [[ -n "${overlayctl_sha256}" ]]; then
        echo "overlayctl-sha256 ${overlayctl_sha256}"
      fi
      "${OVERLAYCTL_BIN}" --help 2>/dev/null | head -n 1 || true
    else
      echo "overlayctl go-run ${ACCOUNTS_REPO}/cmd/overlayctl"
    fi
  } > "${OVERLAY_EVIDENCE_DIR}/tool-versions.txt"
  git -C "${ROOT_DIR}" status --short --branch > "${OVERLAY_EVIDENCE_DIR}/playbooks-git-status.txt" 2>/dev/null || true
  git -C "${ACCOUNTS_REPO}" status --short --branch > "${OVERLAY_EVIDENCE_DIR}/accounts-git-status.txt" 2>/dev/null || true
  if [[ -f "${OVERLAY_STATE_FILE}" ]]; then
    python3 - "${OVERLAY_STATE_FILE}" "${OVERLAY_EVIDENCE_DIR}/state-redacted.json" <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
for key in ("token", "wireguard_private_key"):
    if key in data:
        data[key] = "<redacted>"
print(json.dumps(data, indent=2, sort_keys=True), file=open(sys.argv[2], "w"))
PY
  fi
  if [[ -f "${OVERLAY_CONFIG_FILE}" ]]; then
    python3 - "${OVERLAY_CONFIG_FILE}" "${OVERLAY_EVIDENCE_DIR}/config-redacted.json" <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
transport = data.get("transport")
if isinstance(transport, dict) and "uuid" in transport:
    transport["uuid"] = "<redacted>"
print(json.dumps(data, indent=2, sort_keys=True), file=open(sys.argv[2], "w"))
PY
  fi
}

check_local_tools() {
  missing_required_tools=()
  if ! command -v python3 >/dev/null 2>&1; then
    missing_required_tools+=("python3")
  fi
  if [[ -n "${OVERLAYCTL_BIN:-}" ]]; then
    if [[ ! -x "${OVERLAYCTL_BIN}" ]]; then
      missing_required_tools+=("OVERLAYCTL_BIN executable:${OVERLAYCTL_BIN}")
    fi
  else
    if ! command -v go >/dev/null 2>&1; then
      missing_required_tools+=("go")
    fi
  fi
  if [[ "${OVERLAY_SKIP_DEPLOY}" != "1" ]]; then
    if ! command -v ansible-playbook >/dev/null 2>&1; then
      missing_required_tools+=("ansible-playbook")
    fi
  fi
  if [[ "${OVERLAY_SKIP_UP}" != "1" ]]; then
    if ! command -v wg >/dev/null 2>&1; then
      missing_required_tools+=("wg")
    fi
    if ! command -v wg-quick >/dev/null 2>&1; then
      missing_required_tools+=("wg-quick")
    fi
    if ! command -v xray >/dev/null 2>&1; then
      missing_required_tools+=("xray")
    fi
    if [[ "${OVERLAY_USE_SUDO}" == "1" ]]; then
      if ! command -v sudo >/dev/null 2>&1; then
        missing_required_tools+=("sudo")
      fi
    fi
  fi
  if [[ ${#missing_required_tools[@]} -gt 0 ]]; then
    local name
    for name in "${missing_required_tools[@]}"; do
      echo "missing required tool: ${name}" >&2
    done
    mark_step "preflight.tools" "failed"
    exit 2
  fi
  mark_step "preflight.tools" "ok"
}

check_required_paths() {
  missing_required_paths=()
  if [[ ! -d "${ACCOUNTS_REPO}" ]]; then
    missing_required_paths+=("accounts_repo:${ACCOUNTS_REPO}")
  fi
  if [[ ! -f "${OVERLAY_GROUP_VARS}" ]]; then
    missing_required_paths+=("overlay_group_vars:${OVERLAY_GROUP_VARS}")
  fi
}

report_missing_required_paths() {
  if [[ ${#missing_required_paths[@]} -gt 0 ]]; then
    local path
    for path in "${missing_required_paths[@]}"; do
      echo "missing required path: ${path}" >&2
    done
    mark_step "preflight.paths" "failed"
    exit 2
  fi
  mark_step "preflight.paths" "ok"
}

build_overlayctl_bin() {
  if [[ "${OVERLAY_BUILD_BIN}" != "1" ]]; then
    return
  fi
  if [[ -n "${OVERLAYCTL_BIN:-}" ]]; then
    echo "using explicit OVERLAYCTL_BIN=${OVERLAYCTL_BIN}; skipping overlayctl build"
    return
  fi
  if ! command -v go >/dev/null 2>&1; then
    missing_required_tools+=("go")
    echo "missing required tool: go" >&2
    mark_step "overlayctl.build" "failed"
    exit 2
  fi
  mkdir -p "$(dirname "${OVERLAY_BUILD_BIN_PATH}")"
  if [[ ! -d "${ACCOUNTS_REPO}/cmd/overlayctl" ]]; then
    missing_required_paths+=("overlayctl_package:${ACCOUNTS_REPO}/cmd/overlayctl")
    echo "missing required path: overlayctl_package:${ACCOUNTS_REPO}/cmd/overlayctl" >&2
    mark_step "overlayctl.build" "failed"
    exit 2
  fi
  echo "building overlayctl from ${ACCOUNTS_REPO} to ${OVERLAY_BUILD_BIN_PATH}"
  (
    cd "${ACCOUNTS_REPO}"
    CGO_ENABLED=0 go build -trimpath -o "${OVERLAY_BUILD_BIN_PATH}" ./cmd/overlayctl
  )
  OVERLAYCTL_BIN="${OVERLAY_BUILD_BIN_PATH}"
  export OVERLAYCTL_BIN
  mark_step "overlayctl.build" "ok"
}

check_required_environment() {
  missing_required_envs=()
  local name
  for name in ACCOUNT_EMAIL ACCOUNT_PASSWORD BRIDGE_AUTH_TOKEN; do
    if [[ -z "${!name:-}" ]]; then
      missing_required_envs+=("${name}")
    fi
  done
  if [[ "${OVERLAY_SKIP_DEPLOY}" != "1" ]]; then
    if [[ -z "${VAULT_SERVER_ROOT_ACCESS_TOKEN:-}" && -z "${VAULT_TOKEN:-}" ]]; then
      missing_required_envs+=("VAULT_SERVER_ROOT_ACCESS_TOKEN or VAULT_TOKEN")
    fi
    if [[ -z "${INTERNAL_SERVICE_TOKEN:-}" ]]; then
      missing_required_envs+=("INTERNAL_SERVICE_TOKEN")
    fi
  fi
  if [[ ${#missing_required_envs[@]} -gt 0 ]]; then
    for name in "${missing_required_envs[@]}"; do
      echo "missing required environment: ${name}" >&2
    done
    mark_step "preflight.environment" "failed"
    exit 2
  fi
  mark_step "preflight.environment" "ok"
}

run_overlayctl() {
  if [[ -n "${OVERLAYCTL_BIN:-}" ]]; then
    "${OVERLAYCTL_BIN}" "$@"
    return
  fi
  (cd "${ACCOUNTS_REPO}" && go run ./cmd/overlayctl "$@")
}

run_overlayctl_root() {
  if [[ "${OVERLAY_USE_SUDO}" == "1" ]]; then
    if [[ -n "${OVERLAYCTL_BIN:-}" ]]; then
      sudo -E env HOME="${HOME}" "${OVERLAYCTL_BIN}" "$@"
      return
    fi
    sudo -E env HOME="${HOME}" bash -c 'cd "$1" && shift && "$@"' bash "${ACCOUNTS_REPO}" go run ./cmd/overlayctl "$@"
    return
  fi
  run_overlayctl "$@"
}

cleanup_on_exit() {
  local status=$?
  if [[ ${status} -ne 0 && -n "${active_step}" ]]; then
    mark_step "${active_step}" "failed"
    active_step=""
  fi
  write_evidence "${status}"
  if [[ ${status} -eq 0 && "${OVERLAY_CHECK_ONLY}" != "1" ]]; then
    local evidence_checker="${ROOT_DIR}/scripts/check-wireguard-over-vless-closure-evidence.sh"
    if [[ -x "${evidence_checker}" ]]; then
      echo "closure evidence check pending" > "${OVERLAY_EVIDENCE_DIR}/closure-check.log"
      if ! "${evidence_checker}" "${OVERLAY_EVIDENCE_DIR}" > "${OVERLAY_EVIDENCE_DIR}/closure-check.log" 2>&1; then
        cat "${OVERLAY_EVIDENCE_DIR}/closure-check.log" >&2
        status=1
      fi
    else
      echo "missing evidence checker: ${evidence_checker}" | tee "${OVERLAY_EVIDENCE_DIR}/closure-check.log" >&2
      status=1
    fi
  fi
  local evidence_message="closure evidence: ${OVERLAY_EVIDENCE_DIR}"
  echo "${evidence_message}" >&2
  if [[ ${status} -ne 0 && "${OVERLAY_TEARDOWN_ON_ERROR}" == "1" && "${overlay_runtime_started}" == "1" ]]; then
    echo "closure failed after local runtime start; running overlayctl down because OVERLAY_TEARDOWN_ON_ERROR=1" >&2
    set +e
    run_overlayctl_root down
  fi
  if [[ -n "${run_log_tee_pid:-}" ]]; then
    exec 1>&3 2>&4
    exec 3>&- 4>&-
    wait "${run_log_tee_pid}" 2>/dev/null || true
  fi
  exit "${status}"
}

trap cleanup_on_exit EXIT

state_value() {
  local key="$1"
  if [[ ! -f "${OVERLAY_STATE_FILE}" ]]; then
    return 0
  fi
  python3 -c 'import json, sys; print(str(json.load(open(sys.argv[1])).get(sys.argv[2], "")).strip())' "${OVERLAY_STATE_FILE}" "${key}"
}

attach_args=()
IFS=',' read -r -a attach_hosts <<< "${OVERLAY_ATTACH_TO}"
for host in "${attach_hosts[@]}"; do
  host="${host#"${host%%[![:space:]]*}"}"
  host="${host%"${host##*[![:space:]]}"}"
  if [[ -n "${host}" ]]; then
    attach_args+=(--attach-to "${host}")
  fi
done

register_args=()
if [[ -n "${OVERLAY_REGISTER_ARGS}" ]]; then
  read -r -a register_args <<< "${OVERLAY_REGISTER_ARGS}"
fi
ansible_syntax_args=()
if [[ -n "${OVERLAY_ANSIBLE_SYNTAX_ARGS}" ]]; then
  read -r -a ansible_syntax_args <<< "${OVERLAY_ANSIBLE_SYNTAX_ARGS}"
fi
ansible_deploy_args=()
if [[ -n "${OVERLAY_ANSIBLE_DEPLOY_ARGS}" ]]; then
  read -r -a ansible_deploy_args <<< "${OVERLAY_ANSIBLE_DEPLOY_ARGS}"
fi

check_required_paths
load_env_files
report_missing_required_paths
build_overlayctl_bin
if [[ "${OVERLAY_SKIP_LOCAL_TOOL_CHECK}" != "1" ]]; then
  check_local_tools
else
  mark_step "preflight.tools" "skipped"
fi

check_required_environment
if [[ "${OVERLAY_CHECK_ONLY}" == "1" ]]; then
  mark_step "check_only" "ok"
  echo "closure prerequisites satisfied; exiting because OVERLAY_CHECK_ONLY=1"
  exit 0
fi

echo "[1/10] login to ${ACCOUNTS_SERVICE_URL}"
begin_step "01.login"
run_overlayctl login \
  --server "${ACCOUNTS_SERVICE_URL}" \
  --email "${ACCOUNT_EMAIL}" \
  --password "${ACCOUNT_PASSWORD}"
complete_step "01.login" "ok"

echo "[2/10] register local WireGuard device"
begin_step "02.register_device"
if [[ ${#register_args[@]} -eq 0 ]]; then
  existing_public_key="$(state_value wireguard_public_key)"
  existing_private_key="$(state_value wireguard_private_key)"
  if [[ -n "${existing_public_key}" && -n "${existing_private_key}" ]]; then
    echo "Reusing WireGuard keypair from ${OVERLAY_STATE_FILE}"
    register_args+=(--public-key "${existing_public_key}" --private-key "${existing_private_key}")
  else
    echo "No existing WireGuard keypair found in ${OVERLAY_STATE_FILE}; generating a new one"
    register_args+=(--generate-key)
  fi
fi
run_overlayctl register-device "${register_args[@]}"
complete_step "02.register_device" "ok"

echo "[3/10] sync and render initial overlay config"
begin_step "03.initial_sync_render_preflight"
run_overlayctl sync-config --node-id "${OVERLAY_NODE_ID}"
run_overlayctl render
run_overlayctl preflight
complete_step "03.initial_sync_render_preflight" "ok"

echo "[4/10] project local device into playbooks client peers"
begin_step "04.apply_playbooks_client"
run_overlayctl apply-playbooks-client \
  --group-vars "${OVERLAY_GROUP_VARS}" \
  "${attach_args[@]}"
complete_step "04.apply_playbooks_client" "ok"

if [[ "${OVERLAY_SKIP_DEPLOY}" != "1" ]]; then
  echo "[5/10] deploy WireGuard-over-VLESS gateway path"
  begin_step "05.deploy_gateway"
  (
    cd "${ROOT_DIR}"
    ANSIBLE_CONFIG=ansible.cfg ansible-playbook -i inventory.ini vpn-wireguard-over-vless.yml --syntax-check "${ansible_syntax_args[@]}"
    ANSIBLE_CONFIG=ansible.cfg ansible-playbook -i inventory.ini vpn-wireguard-over-vless.yml "${ansible_deploy_args[@]}"
  )
  complete_step "05.deploy_gateway" "ok"
else
  echo "[5/10] skipped deploy because OVERLAY_SKIP_DEPLOY=1"
  mark_step "05.deploy_gateway" "skipped"
fi

echo "[6/10] refresh config after gateway heartbeat"
begin_step "06.refresh_sync_render_preflight"
run_overlayctl sync-config --node-id "${OVERLAY_NODE_ID}"
run_overlayctl render
run_overlayctl preflight
complete_step "06.refresh_sync_render_preflight" "ok"

if [[ "${OVERLAY_SKIP_UP}" != "1" ]]; then
  echo "[7/10] start local Xray and WireGuard"
  begin_step "07.local_runtime_up"
  run_overlayctl_root up
  overlay_runtime_started=1
  run_overlayctl_root status
  complete_step "07.local_runtime_up" "ok"

  echo "[8/10] verify private bridge connectivity"
  begin_step "08.connectivity"
  run_overlayctl check-connectivity --bearer "${BRIDGE_AUTH_TOKEN}"
  complete_step "08.connectivity" "ok"

  echo "[9/10] acknowledge applied overlay config"
  begin_step "09.ack_config"
  run_overlayctl ack-config
  complete_step "09.ack_config" "ok"

  if [[ "${OVERLAY_TEARDOWN}" == "1" ]]; then
    echo "[10/10] tear down local overlay runtime"
    begin_step "10.teardown"
    run_overlayctl_root down
    overlay_runtime_started=0
    complete_step "10.teardown" "ok"
  else
    echo "[10/10] leaving local overlay runtime up; set OVERLAY_TEARDOWN=1 to tear it down"
    mark_step "10.teardown" "skipped_runtime_left_up"
  fi
else
  echo "[7/10] skipped local runtime start because OVERLAY_SKIP_UP=1"
  echo "[8/10] skipped connectivity check because OVERLAY_SKIP_UP=1"
  echo "[9/10] skipped config ack because OVERLAY_SKIP_UP=1"
  echo "[10/10] skipped teardown because OVERLAY_SKIP_UP=1"
  mark_step "07.local_runtime_up" "skipped"
  mark_step "08.connectivity" "skipped"
  mark_step "09.ack_config" "skipped"
  mark_step "10.teardown" "skipped"
fi
