#!/usr/bin/env bash
set -euo pipefail

: "${TARGET_IP:?TARGET_IP is required}"
: "${COMPONENT:?COMPONENT is required}"
: "${RUN_KEY:?RUN_KEY is required}"
: "${SSH_PRIVATE_KEY_PATH:?SSH_PRIVATE_KEY_PATH is required}"
ssh_args=(-i "${SSH_PRIVATE_KEY_PATH}" -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=15)

ssh "${ssh_args[@]}" "root@${TARGET_IP}" bash -s -- "${COMPONENT}" "${RUN_KEY}" <<'REMOTE'
set -euo pipefail
component="$1"
run_key="$2"
[[ "${run_key}" =~ ^[0-9]+-[0-9]+$ ]]
manifest="/var/lib/observability-migration-backups/${run_key}/${component}/restore-manifest.txt"
test -s "${manifest}"
source_bytes="$(awk -F= '$1=="source_bytes" {print $2}' "${manifest}")"
target_bytes="$(awk -F= '$1=="target_bytes" {print $2}' "${manifest}")"
[[ "${source_bytes}" =~ ^[0-9]+$ && "${source_bytes}" -gt 0 ]]
[[ "${target_bytes}" =~ ^[0-9]+$ && "${target_bytes}" -gt 0 ]]
case "${component}" in
  victoriametrics)
    container=xstream_victoriametrics; path=/vmetrics_data; port=9090; health_path=/health
    ;;
  victorialogs)
    container=xstream_victorialogs; path=/vlogs_data; port=9428; health_path=/metrics
    ;;
  victoriatraces)
    container=xstream_victoriatraces; path=/vtraces_data; port=10428; health_path=/metrics
    ;;
  *) echo "Unsupported component ${component}" >&2; exit 2 ;;
esac
test "$(docker inspect --format '{{.State.Running}}' "${container}")" = true
volume="$(docker inspect --format "{{range .Mounts}}{{if eq .Destination \"${path}\"}}{{.Name}}{{end}}{{end}}" "${container}")"
test -n "${volume}"
volume_path="$(docker volume inspect --format '{{.Mountpoint}}' "${volume}")"
test -d "${volume_path}"
test "$(du -sb "${volume_path}" | awk '{print $1}')" -gt 0
curl --fail --silent --show-error --retry 12 --retry-delay 5 "http://127.0.0.1:${port}${health_path}" >/dev/null
if [[ "${component}" != victoriametrics ]]; then
  partitions="$(find "${volume_path}/partitions" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')"
  source_partitions="$(awk -F= '$1=="source_partition_count" {print $2}' "${manifest}")"
  [[ "${partitions}" -gt 0 && "${source_partitions}" =~ ^[0-9]+$ && "${partitions}" -ge "${source_partitions}" ]]
fi
echo "${component} healthy; source snapshot ${source_bytes} bytes; target store ${target_bytes} bytes."
REMOTE
