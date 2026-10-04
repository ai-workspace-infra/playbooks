#!/usr/bin/env bash
set -euo pipefail

component="${1:?component required}"
run_key="${2:?run key required}"
[[ "${run_key}" =~ ^[0-9]+-[0-9]+$ ]] || { echo "invalid run key" >&2; exit 2; }
staging="/var/tmp/observability-migration/${run_key}/${component}"
backup="/var/lib/observability-migration-backups/${run_key}/${component}"
mkdir -p "${backup}"
initialized=false
container=""
volume_path=""
restore_component() {
  local rc=$?
  if [[ "${rc}" -ne 0 && "${initialized}" == true ]]; then
    echo "Restore failed; restoring the target's pre-migration data from ${backup}." >&2
    docker stop "${container}" >/dev/null 2>&1 || true
    if [[ "${component}" == victoriametrics ]]; then
      find "${volume_path}" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
      cp -a "${backup}/storage/." "${volume_path}/"
    else
      rm -rf "${volume_path}/partitions"
      if [[ -d "${backup}/partitions" ]]; then cp -a "${backup}/partitions" "${volume_path}/partitions"; fi
    fi
  fi
  if [[ -n "${container}" ]]; then docker start "${container}" >/dev/null 2>&1 || true; fi
  if [[ "${rc}" -ne 0 ]]; then exit "${rc}"; fi
}
trap restore_component EXIT

resolve_volume() {
  local destination="$1" volume
  volume="$(docker inspect --format "{{range .Mounts}}{{if eq .Destination \"${destination}\"}}{{.Name}}{{end}}{{end}}" "${container}")"
  [[ -n "${volume}" ]] || { echo "could not resolve target Docker volume ${destination} on ${container}" >&2; exit 1; }
  target_volume="${volume}"
  volume_path="$(docker volume inspect --format '{{.Mountpoint}}' "${volume}")"
}
validate_image_version() {
  source_image="$(awk -F= '$1=="source_image" {print $2}' "${staging}/migration-manifest.txt")"
  target_image="$(docker inspect --format '{{.Config.Image}}' "${container}")"
  [[ -n "${source_image}" && "${source_image}" == "${target_image}" ]] || {
    echo "Refusing to restore ${component} across image versions (source=${source_image:-missing}, target=${target_image})." >&2
    exit 1
  }
}
validate_target_capacity() {
  local source_store_bytes available_bytes required_bytes
  source_store_bytes="$(awk -F= '$1=="source_store_bytes" {print $2}' "${staging}/migration-manifest.txt")"
  [[ "${source_store_bytes}" =~ ^[0-9]+$ && "${source_store_bytes}" -gt 0 ]] || { echo "Invalid source store byte count." >&2; exit 1; }
  target_before_bytes="$(du -sb "${volume_path}" | awk '{print $1}')"
  available_bytes="$(( $(df -Pk "${volume_path}" | awk 'NR==2 {print $4}') * 1024 ))"
  required_bytes="$(( source_store_bytes + target_before_bytes + 1073741824 ))"
  if ((available_bytes < required_bytes)); then
    echo "Insufficient target disk space: available=${available_bytes}, required=${required_bytes} bytes including restore backup and 1 GiB headroom." >&2
    exit 1
  fi
}

case "${component}" in
  victoriametrics)
    container=xstream_victoriametrics; port=9090; health_path=/health
    validate_image_version
    vm_version="${source_image#victoriametrics/victoria-metrics:}"
    [[ "${vm_version}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "Unsupported VictoriaMetrics image tag ${vm_version}" >&2; exit 1; }
    resolve_volume /vmetrics_data
    validate_target_capacity
    docker inspect -f '{{.State.Running}}' "${container}" | grep -qx true
    docker stop "${container}" >/dev/null
    mkdir -p "${backup}/storage"
    cp -a "${volume_path}/." "${backup}/storage/"
    initialized=true
    find "${volume_path}" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
    docker pull "victoriametrics/vmrestore:${vm_version}" >/dev/null
    docker run --rm -v "${target_volume}:/storage" -v "${staging}:/backup:ro" \
      "victoriametrics/vmrestore:${vm_version}" \
      -src=fs:///backup -storageDataPath=/storage
    ;;
  victorialogs|victoriatraces)
    if [[ "${component}" == victorialogs ]]; then
      container=xstream_victorialogs; destination=/vlogs_data; port=9428; health_path=/metrics
    else
      container=xstream_victoriatraces; destination=/vtraces_data; port=10428; health_path=/metrics
    fi
    validate_image_version
    resolve_volume "${destination}"
    validate_target_capacity
    docker inspect -f '{{.State.Running}}' "${container}" | grep -qx true
    [[ -d "${staging}/partitions" ]] || { echo "missing partition snapshots for ${component}" >&2; exit 1; }
    docker stop "${container}" >/dev/null
    if [[ -d "${volume_path}/partitions" ]]; then cp -a "${volume_path}/partitions" "${backup}/partitions"; fi
    initialized=true
    rm -rf "${volume_path}/partitions"
    mkdir -p "${volume_path}/partitions"
    cp -a "${staging}/partitions/." "${volume_path}/partitions/"
    ;;
  *) echo "unsupported component ${component}" >&2; exit 2 ;;
esac

docker start "${container}" >/dev/null
for attempt in $(seq 1 36); do
  if curl --fail --silent --show-error "http://127.0.0.1:${port}${health_path}" >/dev/null; then break; fi
  if [[ "${attempt}" -eq 36 ]]; then echo "${component} did not become healthy after restore" >&2; exit 1; fi
  sleep 5
done
if [[ "${component}" == victoriametrics ]]; then
  query="$(curl --fail --silent --show-error --get --data-urlencode 'query=up' http://127.0.0.1:9090/api/v1/query)"
  python3 -c 'import json,sys; data=json.load(sys.stdin); assert data.get("status") == "success", data' <<<"${query}"
else
  count="$(find "${volume_path}/partitions" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')"
  [[ "${count}" -gt 0 ]] || { echo "${component} restored no data partitions" >&2; exit 1; }
fi
printf 'component=%s\nsource_bytes=%s\nsource_store_bytes=%s\nsource_partition_count=%s\ntarget_before_bytes=%s\ntarget_bytes=%s\nbackup_path=%s\nsource_image=%s\n' \
  "${component}" \
  "$(awk -F= '$1=="source_bytes" {print $2}' "${staging}/migration-manifest.txt")" \
  "$(awk -F= '$1=="source_store_bytes" {print $2}' "${staging}/migration-manifest.txt")" \
  "$(awk -F= '$1=="partition_count" {print $2}' "${staging}/migration-manifest.txt")" \
  "${target_before_bytes}" \
  "$(du -sb "${volume_path}" | awk '{print $1}')" \
  "${backup}" \
  "${source_image}" > "${backup}/restore-manifest.txt"
rm -rf "${staging}"
initialized=false
trap - EXIT
echo "Restored and health-checked ${component}; previous target files are retained at ${backup}."
