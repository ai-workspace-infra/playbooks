#!/usr/bin/env bash
set -euo pipefail

: "${SOURCE_IP:?SOURCE_IP is required}"
: "${TARGET_IP:?TARGET_IP is required}"
: "${COMPONENT:?COMPONENT is required}"
: "${MIGRATION_MODE:?MIGRATION_MODE is required}"
: "${RUN_KEY:?RUN_KEY is required}"
: "${SSH_PRIVATE_KEY_PATH:?SSH_PRIVATE_KEY_PATH is required}"

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"

[[ "${SOURCE_IP}" =~ ^[0-9.]+$ && "${TARGET_IP}" =~ ^[0-9.]+$ ]] || { echo "Source and target must be IPv4 addresses." >&2; exit 2; }
[[ "${SOURCE_IP}" != "${TARGET_IP}" ]] || { echo "Source and target IPs must differ." >&2; exit 2; }
[[ "${COMPONENT}" == victoriametrics || "${COMPONENT}" == victorialogs || "${COMPONENT}" == victoriatraces ]] || { echo "Unsupported component." >&2; exit 2; }
[[ "${MIGRATION_MODE}" == baseline || "${MIGRATION_MODE}" == final ]] || { echo "Unsupported migration mode." >&2; exit 2; }
[[ "${RUN_KEY}" =~ ^[0-9]+-[0-9]+$ ]] || { echo "Invalid run key." >&2; exit 2; }

ssh_args=(-i "${SSH_PRIVATE_KEY_PATH}" -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=15)
local_root="${RUNNER_TEMP}/observability-migration"
local_component="${local_root}/${RUN_KEY}/${COMPONENT}"
remote_root="/var/tmp/observability-migration/${RUN_KEY}"
mkdir -p "${local_component}"
cleanup() {
  ssh "${ssh_args[@]}" "root@${SOURCE_IP}" "rm -rf '${remote_root}'" >/dev/null 2>&1 || true
  rm -rf "${local_root:?}/${RUN_KEY:?}"
}
trap cleanup EXIT

echo "Creating ${MIGRATION_MODE} snapshot for ${COMPONENT} from source ${SOURCE_IP}."
ssh "${ssh_args[@]}" "root@${SOURCE_IP}" "bash -s -- '${COMPONENT}' '${RUN_KEY}' '${MIGRATION_MODE}'" \
  < "${script_dir}/observability_data_source_stage.sh"

ssh "${ssh_args[@]}" "root@${SOURCE_IP}" "tar -C /var/tmp/observability-migration/${RUN_KEY} -cf - '${COMPONENT}'" \
  | tar -C "${local_root}/${RUN_KEY}" -xf -
[[ -s "${local_component}/migration-manifest.txt" ]] || { echo "Source snapshot manifest is missing." >&2; exit 1; }
source_bytes="$(awk -F= '$1=="source_bytes" {print $2}' "${local_component}/migration-manifest.txt")"
[[ "${source_bytes}" =~ ^[0-9]+$ && "${source_bytes}" -gt 0 ]] || { echo "Source snapshot has no bytes." >&2; exit 1; }

ssh "${ssh_args[@]}" "root@${TARGET_IP}" "mkdir -p '${remote_root}'"
tar -C "${local_root}" -cf - "${RUN_KEY}/${COMPONENT}" \
  | ssh "${ssh_args[@]}" "root@${TARGET_IP}" "tar -C /var/tmp/observability-migration -xf -"

echo "Restoring ${COMPONENT} on target ${TARGET_IP}; preserving the pre-migration target data."
ssh "${ssh_args[@]}" "root@${TARGET_IP}" "bash -s -- '${COMPONENT}' '${RUN_KEY}'" \
  < "${script_dir}/observability_data_target_restore.sh"

echo "${COMPONENT} migration complete; source snapshot bytes=${source_bytes}."
