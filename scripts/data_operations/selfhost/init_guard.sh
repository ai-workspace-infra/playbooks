#!/usr/bin/env bash
set -euo pipefail

target="${1:-}"
cmdb_file="${CMDB_FILE:-cmdb/cmdb.json}"
environment="${REQUESTED_ENVIRONMENT:-}"
[[ "${environment}" =~ ^(uat|prod|sit)$ ]] || { echo "invalid environment" >&2; exit 2; }
. "$(dirname "${BASH_SOURCE[0]}")/cmdb-ssh-login.sh"
selfhost_cmdb_ssh_login "${cmdb_file}" "${target}" "${environment}"

ssh_opts=(-i "$HOME/.ssh/id_deploy" -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new)
remote_command=("${sudo_prefix[@]}" bash -s -- "$environment")
printf -v command '%q ' "${remote_command[@]}"
ssh "${ssh_opts[@]}" "${ssh_user}@${host_ip}" "$command" < "$(dirname "${BASH_SOURCE[0]}")/init_guard_host.sh"
