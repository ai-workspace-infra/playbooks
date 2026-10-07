#!/usr/bin/env bash
set -euo pipefail

is_true() {
  [[ "${1}" == 'true' ]]
}

require_host() {
  [[ -n "${ACTION_MATRIX_HOST}" ]] || {
    echo '::error::matrix_host is required for this deployment-runner operation.' >&2
    exit 1
  }
}

resolve_host_ip() {
  require_host
  [[ -f "${ACTION_CMDB_FILE}" ]] || {
    echo "::error::CMDB file not found: ${ACTION_CMDB_FILE}" >&2
    exit 1
  }
  target_ip="$(jq -r --arg host "${ACTION_MATRIX_HOST}" '.[$host].ip // empty' "${ACTION_CMDB_FILE}")"
  [[ -n "${target_ip}" ]] || {
    echo "::error::No IP for ${ACTION_MATRIX_HOST} in ${ACTION_CMDB_FILE}" >&2
    exit 1
  }
  target_user="$(jq -r --arg host "${ACTION_MATRIX_HOST}" '.[$host].ansible_user // "root"' "${ACTION_CMDB_FILE}")"
  [[ -n "${target_user}" && "${target_user}" != "null" ]] || {
    echo "::error::No SSH user for ${ACTION_MATRIX_HOST} in ${ACTION_CMDB_FILE}" >&2
    exit 1
  }
  target_port="$(jq -r --arg host "${ACTION_MATRIX_HOST}" '.[$host].ansible_port // .[$host].host_vars.ansible_port // 22' "${ACTION_CMDB_FILE}")"
}

configure_ssh_key() {
  [[ -n "${ACTION_SSH_KEY_B64}" ]] || {
    echo '::error::ssh_key_b64 is required when configuring SSH.' >&2
    exit 1
  }

  mkdir -p "${HOME}/.ssh"
  printf '%s' "${ACTION_SSH_KEY_B64}" | base64 -d > "${HOME}/.ssh/id_deploy"
  chmod 600 "${HOME}/.ssh/id_deploy"
  ssh-keygen -y -f "${HOME}/.ssh/id_deploy" >/dev/null

  if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    printf 'deploy_key_file=%s\n' "${HOME}/.ssh/id_deploy" >>"${GITHUB_OUTPUT}"
  fi
}

ssh_options=()
configure_ssh_options() {
  local connect_timeout="${1:-10}"
  ssh_options=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o BatchMode=yes -o "ConnectTimeout=${connect_timeout}")
  ssh_options+=(-p "${target_port}")
  if [[ -f "${HOME}/.ssh/id_deploy" ]]; then
    ssh_options+=(-i "${HOME}/.ssh/id_deploy")
  fi
}

# ConnectTimeout only bounds the TCP connection phase.  A node that accepts a
# connection but never completes the SSH banner/key exchange can otherwise
# keep the release job running forever.  Bound every probe so the outer retry
# loop retains ownership of the deployment timeout.
ssh_with_timeout() {
  local timeout_seconds="${1:?SSH command timeout is required}"
  shift
  command timeout --foreground --kill-after=2s "${timeout_seconds}s" ssh "$@"
}

wait_for_ssh() {
  resolve_host_ip
  configure_ssh_options 5
  # Fresh cloud VMs can take several minutes to finish first-boot networking
  # and start sshd. Keep this bounded, but don't fail normal deployments just
  # because cloud-init exceeded the former three-minute window.
  # Terraform may stop/start a GCP Spot VM while changing its machine type or
  # external IP. RUNNING and OS Login key registration can complete before
  # guest networking and sshd have converged, so allow one bounded 20-minute
  # window for that first boot rather than failing a valid UAT rollout at 10m.
  local timeout_secs="${HOST_SSH_WAIT_TIMEOUT:-1200}"
  # A rejected key means sshd is up and the network path works; only the
  # credential is wrong, so waiting out the full boot window cannot help.
  # Keep a short grace for OS Login key propagation, then fail with the cause.
  local auth_grace_secs="${HOST_SSH_AUTH_FAILURE_GRACE:-300}"
  local deadline=$((SECONDS + timeout_secs))
  local err_file last_err='' err='' auth_failed_since=''
  err_file="$(mktemp)"
  echo "Waiting for SSH to become ready on ${ACTION_MATRIX_HOST} (${target_user}@${target_ip}:${target_port})..."
  while ((SECONDS < deadline)); do
    if ssh_with_timeout 12 "${ssh_options[@]}" "${target_user}@${target_ip}" true 2>"${err_file}"; then
      rm -f "${err_file}"
      echo "SSH is ready on ${ACTION_MATRIX_HOST} (${target_ip}:${target_port})."
      return
    fi
    # The probe is bounded by timeout(1), which exits silently on expiry.
    err="$(grep -v '^Warning: Permanently added' "${err_file}" | tail -n 1 || true)"
    [[ -n "${err}" ]] || err='no response before the SSH probe timeout (banner or key exchange stalled)'
    if [[ "${err}" != "${last_err}" ]]; then
      echo "waiting: ${err}"
      last_err="${err}"
    fi
    if [[ "${err}" == *'Permission denied'* || "${err}" == *'Too many authentication failures'* ]]; then
      auth_failed_since="${auth_failed_since:-${SECONDS}}"
      if ((SECONDS - auth_failed_since >= auth_grace_secs)); then
        rm -f "${err_file}"
        echo "::error::SSH on ${ACTION_MATRIX_HOST} (${target_ip}:${target_port}) is reachable but kept rejecting the deploy key for ${auth_grace_secs}s: ${err}" >&2
        exit 1
      fi
    else
      auth_failed_since=''
    fi
    sleep 3
  done

  rm -f "${err_file}"
  echo "::error::Timed out waiting for SSH on ${ACTION_MATRIX_HOST} (${target_ip}:${target_port}) after ${timeout_secs}s; last error: ${last_err:-none}" >&2
  exit 1
}

wait_for_package_init() {
  resolve_host_ip
  configure_ssh_options 10
  local timeout_secs="${HOST_INIT_WAIT_TIMEOUT:-120}"
  local interval_secs=3
  local privileged_shell='bash -s'
  [[ "${target_user}" == "root" ]] || privileged_shell='sudo -n bash -s'

  case "${ACTION_PACKAGE_INIT_POLICY}" in
    disable-unattended-upgrades)
      echo "Applying package-init policy disable-unattended-upgrades on ${ACTION_MATRIX_HOST} (${target_ip}:${target_port})..."
      ssh_with_timeout 15 "${ssh_options[@]}" "${target_user}@${target_ip}" "${privileged_shell}" <<'REMOTE' 2>/dev/null || true
    if command -v systemctl >/dev/null 2>&1; then
      systemctl stop unattended-upgrades.service >/dev/null 2>&1 || true
      systemctl disable unattended-upgrades.service >/dev/null 2>&1 || true
    fi
    pkill -f unattended-upgrade >/dev/null 2>&1 || true
REMOTE
      ;;
    preserve)
      echo "Preserving unattended-upgrades policy on ${ACTION_MATRIX_HOST} (${target_ip}:${target_port})."
      ;;
    *)
      echo "::error::package_init_policy must be preserve or disable-unattended-upgrades" >&2
      exit 1
      ;;
  esac

  local probe
  probe="$(cat <<'REMOTE'
for lock in /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock /var/lib/apt/lists/lock; do
  [ -e "$lock" ] || continue
  if command -v fuser >/dev/null 2>&1 && fuser "$lock" >/dev/null 2>&1; then
    echo "HELD:$lock"
    exit 1
  fi
done
if pgrep -x unattended-upgr >/dev/null 2>&1; then
  echo 'HELD:unattended-upgrades'
  exit 1
fi
echo READY
REMOTE
)"

  local deadline=$((SECONDS + timeout_secs)) last='' out=''
  while ((SECONDS < deadline)); do
    if out="$(ssh_with_timeout 15 "${ssh_options[@]}" "${target_user}@${target_ip}" "${privileged_shell}" <<<"${probe}" 2>/dev/null)" && [[ "${out}" == *READY* ]]; then
      echo "Host ${ACTION_MATRIX_HOST} (${target_ip}:${target_port}) finished first-boot package work."
      return
    fi
    [[ "${out}" == "${last}" ]] || {
      [[ -n "${out}" ]] && echo "waiting: ${out}"
      last="${out}"
    }
    sleep "${interval_secs}"
  done

  echo "::warning::${ACTION_MATRIX_HOST} (${target_ip}:${target_port}) still had package locks held after ${timeout_secs}s; continuing and relying on apt lock_timeout."
}

install_ansible() {
  if command -v ansible >/dev/null 2>&1 && python3 -c 'import hvac' >/dev/null 2>&1; then
    echo 'Ansible and hvac are already available on the deployment runner.'
    return
  fi

  local pip_args=(
    --quiet
    --disable-pip-version-check
    --retries "${PIP_INSTALL_RETRIES:-5}"
    --timeout "${PIP_INSTALL_TIMEOUT_SECONDS:-60}"
  )
  if python3 -m pip install --help 2>&1 | grep -q -- '--break-system-packages'; then
    pip_args+=(--break-system-packages)
  fi
  local attempt=1 max_attempts="${PIP_INSTALL_ATTEMPTS:-3}"
  until python3 -m pip install "${pip_args[@]}" ansible hvac; do
    if ((attempt >= max_attempts)); then
      echo "::error::Unable to install Ansible/hvac after ${attempt} attempts." >&2
      return 1
    fi
    echo "::warning::Ansible/hvac installation attempt ${attempt} failed; retrying." >&2
    attempt=$((attempt + 1))
    sleep 5
  done
  python3 -c 'import hvac'
}

assert_ansible_target() {
  require_host
  [[ -f "${ACTION_ANSIBLE_INVENTORY}" ]] || {
    echo "::error::inventory file not found: ${ACTION_ANSIBLE_INVENTORY} (cwd=$(pwd))" >&2
    exit 1
  }

  local ping_out
  ping_out="$(ansible -i "${ACTION_ANSIBLE_INVENTORY}" "${ACTION_MATRIX_HOST}" -m ping 2>&1 || true)"
  echo "${ping_out}"
  if ! grep -q SUCCESS <<<"${ping_out}"; then
    echo "::error::Ansible target '${ACTION_MATRIX_HOST}' matched no reachable host in ${ACTION_ANSIBLE_INVENTORY}; refusing to report a no-op deploy as success." >&2
    exit 1
  fi
}

if [[ -n "${ACTION_SSH_KEY_B64}" ]]; then
  configure_ssh_key
fi
if is_true "${ACTION_WAIT_FOR_SSH}"; then
  wait_for_ssh
fi
if is_true "${ACTION_WAIT_FOR_PACKAGE_INIT}"; then
  wait_for_package_init
fi
if is_true "${ACTION_INSTALL_ANSIBLE}"; then
  install_ansible
fi
if is_true "${ACTION_ASSERT_ANSIBLE_TARGET}"; then
  assert_ansible_target
fi
