#!/usr/bin/env bash
set -euo pipefail

component="${1:?component required}"
run_key="${2:?run key required}"
migration_mode="${3:?migration mode required}"
[[ "${run_key}" =~ ^[0-9]+-[0-9]+$ ]] || { echo "invalid run key" >&2; exit 2; }
[[ "${migration_mode}" == baseline || "${migration_mode}" == final ]] || { echo "invalid migration mode" >&2; exit 2; }

root="/var/tmp/observability-migration/${run_key}/${component}"
mkdir -p "${root}"
partition_count=0
active_snapshots=()
snapshot_endpoint=""
source_stopped=false
cleanup() {
  for snapshot in "${active_snapshots[@]}"; do
    curl --fail --silent --show-error -X POST --get --data-urlencode "path=${snapshot}" \
      "${snapshot_endpoint}" >/dev/null 2>&1 || true
  done
  docker rm -f "${snapshot_container:-}" >/dev/null 2>&1 || true
  if [[ "${source_stopped}" == true ]]; then
    docker start "${container}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

resolve_volume() {
  local container="$1" destination="$2" volume
  volume="$(docker inspect --format "{{range .Mounts}}{{if eq .Destination \"${destination}\"}}{{.Name}}{{end}}{{end}}" "${container}")"
  [[ -n "${volume}" ]] || { echo "could not resolve Docker volume ${destination} on ${container}" >&2; exit 1; }
  printf '%s' "${volume}"
}

case "${component}" in
  victoriametrics)
    container=xstream_victoriametrics
    volume="$(resolve_volume "${container}" /vmetrics_data)"
    volume_path="$(docker volume inspect --format '{{.Mountpoint}}' "${volume}")"
    docker inspect -f '{{.State.Running}}' "${container}" | grep -qx true
    source_image="$(docker inspect --format '{{.Config.Image}}' "${container}")"
    [[ "${source_image}" =~ ^victoriametrics/victoria-metrics:(v[0-9]+\.[0-9]+\.[0-9]+)$ ]] || {
      echo "Unsupported VictoriaMetrics source image: ${source_image}" >&2; exit 1;
    }
    vm_version="${BASH_REMATCH[1]}"
    snapshot_container="obs-vmbackup-${run_key}"
    docker pull "victoriametrics/vmbackup:${vm_version}" >/dev/null
    docker run --rm --name "${snapshot_container}" --network "container:${container}" \
      -v "${volume}:/storage:ro" -v "${root}:/backup" \
      "victoriametrics/vmbackup:${vm_version}" \
      -storageDataPath=/storage \
      -snapshot.createURL=http://localhost:8428/snapshot/create \
      -dst=fs:///backup
    snapshot_container=""
    ;;
  victorialogs|victoriatraces)
    if [[ "${component}" == victorialogs ]]; then
      container=xstream_victorialogs; destination=/vlogs_data; port=9428
    else
      container=xstream_victoriatraces; destination=/vtraces_data; port=10428
    fi
    volume="$(resolve_volume "${container}" "${destination}")"
    source_image="$(docker inspect --format '{{.Config.Image}}' "${container}")"
    volume_path="$(docker volume inspect --format '{{.Mountpoint}}' "${volume}")"
    docker inspect -f '{{.State.Running}}' "${container}" | grep -qx true
    mkdir -p "${root}/partitions"
    mapfile -t days < <(find "${volume_path}/partitions" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort)
    ((${#days[@]} > 0)) || { echo "${component} source has no data partitions" >&2; exit 1; }
    partition_count="${#days[@]}"
    if [[ "${component}" == victoriatraces ]]; then
      # VictoriaTraces v0.1.0 has no partition snapshot endpoint. Stop the
      # source container and copy its partition tree while quiesced so the
      # filesystem snapshot is consistent; cleanup restarts the service.
      docker stop "${container}" >/dev/null
      source_stopped=true
      cp -a "${volume_path}/partitions/." "${root}/partitions/"
      docker start "${container}" >/dev/null
      source_stopped=false
    else
    for day in "${days[@]}"; do
      [[ "${day}" =~ ^[0-9]{8}$ ]] || { echo "unexpected partition name ${day}" >&2; exit 1; }
      endpoint="http://127.0.0.1:${port}/internal/partition/snapshot/create?partition_prefix=${day}"
      mapfile -t snapshots < <(curl --fail --silent --show-error --retry 3 -X POST "${endpoint}" | python3 -c 'import json,sys; values=json.load(sys.stdin); print("\n".join(values))')
      ((${#snapshots[@]} > 0)) || { echo "no snapshot returned for ${component} partition ${day}" >&2; exit 1; }
      snapshot_endpoint="http://127.0.0.1:${port}/internal/partition/snapshot/delete"
      active_snapshots=("${snapshots[@]}")
      for snapshot in "${snapshots[@]}"; do
        mkdir -p "${root}/partitions/${day}"
        docker cp "${container}:${snapshot}/." "${root}/partitions/${day}/"
        curl --fail --silent --show-error -X POST --get \
          --data-urlencode "path=${snapshot}" \
          "${snapshot_endpoint}" >/dev/null
        active_snapshots=("${active_snapshots[@]:1}")
      done
    done
    fi
    ;;
  *) echo "unsupported component ${component}" >&2; exit 2 ;;
esac

bytes="$(du -sb "${root}" | awk '{print $1}')"
source_store_bytes="$(du -sb "${volume_path}" | awk '{print $1}')"
[[ "${bytes}" =~ ^[0-9]+$ && "${bytes}" -gt 0 ]] || { echo "snapshot output is empty for ${component}" >&2; exit 1; }
[[ "${source_store_bytes}" =~ ^[0-9]+$ && "${source_store_bytes}" -gt 0 ]] || { echo "source store is empty for ${component}" >&2; exit 1; }
printf 'component=%s\nmigration_mode=%s\nsource_bytes=%s\nsource_store_bytes=%s\npartition_count=%s\ncreated_at=%s\n' \
  "${component}" "${migration_mode}" "${bytes}" "${source_store_bytes}" "${partition_count}" "$(date -u +%FT%TZ)" > "${root}/migration-manifest.txt"
printf 'source_image=%s\n' "${source_image}" >> "${root}/migration-manifest.txt"
echo "Prepared ${component} snapshot (${bytes} bytes)."
