#!/usr/bin/env bash
set -euo pipefail
umask 077
SELFHOST_SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PLAYBOOK_ROOT=$(CDPATH= cd -- "$SELFHOST_SCRIPT_DIR/../.." && pwd)
die() { printf 'error: %s\n' "$*" >&2; exit 2; }
need_cmd() { command -v "$1" >/dev/null 2>&1 || die "missing command: $1"; }
need_file() { [[ -f "$1" ]] || die "file not found: $1"; }
valid_env() { [[ "$1" =~ ^[a-z0-9][a-z0-9-]{0,31}$ ]] || die "invalid environment: $1"; }
valid_id() { [[ "$1" =~ ^[a-z0-9][a-z0-9-]{1,63}$ ]] || die "invalid deployment or node id: $1"; }
valid_host() {
  [[ "$1" != *://* && "$1" != */* && "$1" != *:* && "$1" != *' '* ]] || die "hostname must not contain scheme, port, path, or spaces: $1"
  [[ "$1" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]{0,253}[A-Za-z0-9])?$ ]] || die "invalid hostname: $1"
}
valid_class() { [[ "$1" == free || "$1" == plus || "$1" == team ]] || die "resource class must be free, plus, or team"; }
require_common() {
  need_cmd ansible-playbook
  valid_env "$ENVIRONMENT"; valid_id "$DEPLOYMENT_ID"
  need_file "$MANIFEST"; need_file "$INVENTORY"; need_file "$VARS_FILE"
}
run_playbook() {
  local playbook=$1; shift
  need_file "$PLAYBOOK_ROOT/$playbook"
  local args=(-i "$INVENTORY" "$PLAYBOOK_ROOT/$playbook" -e "@$VARS_FILE" -e "deployment_environment=$ENVIRONMENT" -e "prod_v2_deployment_id=$DEPLOYMENT_ID" -e "prod_v2_manifest=$MANIFEST")
  args+=("$@"); ansible-playbook "${args[@]}"
}
receipt() {
  local name=$1 status=$2 dir
  dir=$(dirname "$MANIFEST")/receipts
  [[ -n "$RECEIPT_DIR" ]] && dir=$RECEIPT_DIR
  mkdir -p "$dir"
  local path="$dir/$DEPLOYMENT_ID-$name.json"
  cat >"$path" <<EOF
{"environment":"$ENVIRONMENT","deployment_id":"$DEPLOYMENT_ID","operation":"$name","status":"$status","manifest":"$MANIFEST","inventory":"$INVENTORY"}
EOF
  chmod 0600 "$path"; printf 'receipt: %s\n' "$path"
}

