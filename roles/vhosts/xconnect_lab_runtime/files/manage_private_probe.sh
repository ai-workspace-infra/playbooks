#!/usr/bin/env bash
set -euo pipefail

operation="${1:?operation is required}"
run_id="${2:?run id is required}"
private_address="${3:?private address is required}"
interface="${4:?interface is required}"
port="${5:?port is required}"
printf '%s\n' "$run_id" | grep -Eq '^xcl-[0-9]+-[0-9]+$'
printf '%s\n' "$private_address" | grep -Eq '^[0-9]+(\.[0-9]+){3}$'
printf '%s\n' "$interface" | grep -Eq '^[A-Za-z0-9_.-]{1,15}$'
printf '%s\n' "$port" | grep -Eq '^[0-9]+$'
(( port >= 1024 && port <= 65535 ))

runtime_root="${XCONNECT_PRIVATE_PROBE_RUNTIME_ROOT:-/run}"
[[ "$runtime_root" == /* && "$runtime_root" != *..* ]]
unit="xconnect-lab-probe-${run_id}"
probe_dir="${runtime_root%/}/xconnect-one-${run_id}"

cleanup() {
  local load_state
  if ! load_state="$(systemctl show -p LoadState --value "$unit.service" 2>/dev/null)"; then
    echo "unable to inspect private probe unit $unit.service" >&2
    return 1
  fi
  case "$load_state" in
    not-found) ;;
    loaded|masked|stub|merged)
      if ! systemctl stop "$unit.service" >/dev/null; then
        echo "unable to stop private probe unit $unit.service" >&2
        return 1
      fi
      ;;
    *)
      echo "unexpected private probe unit state for $unit.service: ${load_state:-empty}" >&2
      return 1
      ;;
  esac
  rm -rf -- "$probe_dir"
}

if [[ "$operation" == cleanup ]]; then
  cleanup
  echo "private_probe=removed run=$run_id"
  exit 0
fi
[[ "$operation" == setup ]]
setup_complete=false
cleanup_failed_setup() {
  local status=$?
  if [[ "$setup_complete" != true ]]; then
    cleanup || echo "private probe cleanup after failed setup did not complete" >&2
  fi
  exit "$status"
}
trap cleanup_failed_setup EXIT
ip -4 -o addr show dev "$interface" |
  awk -v expected="${private_address}/32" '$4 == expected {found=1} END {exit(found ? 0 : 1)}'
cleanup
install -d -m 0755 "$probe_dir"
printf '%s\n' "$run_id" > "$probe_dir/index.html"
systemd-run --unit "$unit" --collect --quiet --property RuntimeMaxSec=3600 \
  python3 -m http.server "$port" --bind "$private_address" --directory "$probe_dir"
for _attempt in {1..10}; do
  marker="$(curl --fail --silent --show-error --max-time 2 "http://${private_address}:${port}/" 2>/dev/null || true)"
  if systemctl is-active --quiet "$unit.service" && [[ "$marker" == "$run_id" ]]; then
    setup_complete=true
    trap - EXIT
    echo "private_probe=ready run=$run_id address=$private_address port=$port"
    exit 0
  fi
  if systemctl is-failed --quiet "$unit.service"; then
    break
  fi
  sleep 1
done
echo 'run-scoped private probe did not become ready with the exact marker' >&2
exit 1
