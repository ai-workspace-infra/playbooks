#!/usr/bin/env bash
# Operator-only Vault data guardrails. Never run from GitHub Actions.
set -euo pipefail
umask 077

usage() {
  cat <<'EOF'
Usage: vault_operator_data.sh COMMAND [OPTIONS]

Commands:
  snapshot-save --output FILE
  snapshot-inspect --input FILE
  snapshot-restore --input FILE --confirm-cluster-id ID
  migrate-offline --config FILE --destination-path DIR --source-health-url URL --confirm MIGRATE-POSTGRESQL-TO-RAFT

VAULT_ADDR and VAULT_TOKEN come from a protected, interactive operator
session. Never pass tokens, PostgreSQL passwords, or unseal keys as arguments.
EOF
}

die() { echo "$*" >&2; exit 2; }
command_name="${1:-}"
[[ -n "$command_name" ]] || { usage >&2; exit 2; }
shift
input=""
output=""
config=""
destination_path=""
source_health_url=""
confirm=""
cluster_id=""
while (($#)); do
  case "$1" in
    --input) input="${2:-}"; shift 2 ;;
    --output) output="${2:-}"; shift 2 ;;
    --config) config="${2:-}"; shift 2 ;;
    --destination-path) destination_path="${2:-}"; shift 2 ;;
    --source-health-url) source_health_url="${2:-}"; shift 2 ;;
    --confirm) confirm="${2:-}"; shift 2 ;;
    --confirm-cluster-id) cluster_id="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown option: $1" ;;
  esac
done

command -v vault >/dev/null 2>&1 || die "vault CLI is required"
case "$command_name" in
  snapshot-save)
    [[ -n "$output" && -z "$input$config$confirm$cluster_id" ]] || die "snapshot-save requires only --output"
    [[ -n "${VAULT_ADDR:-}" && -n "${VAULT_TOKEN:-}" ]] || die "VAULT_ADDR and VAULT_TOKEN are required"
    [[ ! -e "$output" && ! -L "$output" ]] || die "Refusing to overwrite existing snapshot"
    output_dir="$(dirname -- "$output")"
    [[ -d "$output_dir" && ! -L "$output_dir" ]] || die "Snapshot parent directory must exist and not be a symlink"
    temporary="$(mktemp "${output_dir}/.vault-snapshot.XXXXXXXX")"
    trap 'rm -f -- "$temporary"' EXIT
    vault operator raft snapshot save "$temporary"
    vault operator raft snapshot inspect "$temporary" >/dev/null
    [[ ! -e "$output" && ! -L "$output" ]] || die "Snapshot target appeared during export"
    chmod 0600 "$temporary"
    ln -- "$temporary" "$output"
    rm -f -- "$temporary"
    [[ -s "$output" ]] || die "Snapshot export is empty"
    echo "Raft snapshot saved and inspected: $output"
    ;;
  snapshot-inspect)
    [[ -f "$input" && ! -L "$input" ]] || die "Snapshot must be a regular file"
    vault operator raft snapshot inspect "$input"
    ;;
  snapshot-restore)
    [[ -f "$input" && ! -L "$input" && -n "$cluster_id" ]] || die "snapshot-restore requires --input and --confirm-cluster-id"
    [[ -n "${VAULT_ADDR:-}" && -n "${VAULT_TOKEN:-}" ]] || die "VAULT_ADDR and VAULT_TOKEN are required"
    command -v jq >/dev/null 2>&1 || die "jq is required"
    status="$(vault status -format=json)" || die "Destination Vault must be reachable and unsealed"
    actual_id="$(printf '%s' "$status" | jq -er '.cluster_id')"
    [[ "$actual_id" == "$cluster_id" ]] || die "Destination cluster ID does not match explicit confirmation"
    printf '%s' "$status" | jq -e '.initialized == true and .sealed == false' >/dev/null || die "Destination Vault is not initialized and unsealed"
    vault operator raft snapshot inspect "$input" >/dev/null
    echo "Restoring a snapshot overwrites destination Vault data; restore completion is asynchronous." >&2
    vault operator raft snapshot restore "$input"
    echo "Restore accepted; verify Vault logs, seal state and application data before reopening traffic."
    ;;
  migrate-offline)
    [[ -f "$config" && ! -L "$config" && -n "$destination_path" && -n "$source_health_url" ]] || die "migrate-offline requires --config, --destination-path and --source-health-url"
    [[ "$confirm" == "MIGRATE-POSTGRESQL-TO-RAFT" ]] || die "Offline migration requires explicit confirmation"
    [[ "$source_health_url" == http://* || "$source_health_url" == https://* ]] || die "Source health URL must be HTTP(S)"
    command -v python3 >/dev/null 2>&1 || die "python3 is required to check config permissions"
    python3 - "$config" "$destination_path" <<'PY'
import os
import stat
import sys
path = sys.argv[1]
mode = os.stat(path).st_mode
if not stat.S_ISREG(mode) or mode & 0o077:
    raise SystemExit("Migration config must be a private regular file (mode 0600)")
with open(path, encoding="utf-8") as stream:
    config = stream.read()
if 'storage_source "postgresql"' not in config or 'storage_destination "raft"' not in config:
    raise SystemExit("Migration config must explicitly name PostgreSQL source and Raft destination")
destination = sys.argv[2]
if not os.path.isdir(destination) or os.path.islink(destination) or os.listdir(destination):
    raise SystemExit("Migration destination must be an existing, empty, non-symlink directory")
PY
    command -v curl >/dev/null 2>&1 || die "curl is required"
    if curl --silent --show-error --max-time 5 --output /dev/null "$source_health_url" 2>/dev/null; then
      die "Source Vault still responds; stop it and freeze writes before offline migration"
    fi
    echo "Source health is unreachable; operator must independently verify PostgreSQL backup, source shutdown, empty Raft destination and rollback plan." >&2
    vault operator migrate -config="$config"
    echo "Storage copied. Start the destination, manually unseal with the original shares, then verify data and join peers before traffic cutover."
    ;;
  *) usage >&2; die "Unknown command: $command_name" ;;
esac
