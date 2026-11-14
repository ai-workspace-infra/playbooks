#!/bin/bash
# Shared settings for QMD Hub scripts (sourced). Managed by Ansible role qmd_hub.
set -euo pipefail
QMD_HUB_ROOT="${QMD_HUB_ROOT:-/opt/qmd-hub}"
QMD_HUB_ETC="${QMD_HUB_ETC:-/etc/qmd-hub}"
# shellcheck disable=SC1091
. "$QMD_HUB_ETC/hub.env"
DATA="$QMD_HUB_ROOT/data/origins"
STATE="$QMD_HUB_ROOT/state"
psql_hub() {
  psql -X -q -At -v ON_ERROR_STOP=1 -h /var/run/postgresql -p "$QMD_HUB_PG_PORT" -d "$QMD_HUB_PG_DATABASE" "$@"
}
log() { printf '%s %s\n' "$(date -Is)" "$*" >&2; }
