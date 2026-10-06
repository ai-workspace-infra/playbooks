#!/usr/bin/env bash
set -euo pipefail
action="${1:-}"
[[ "$action" == stop || "$action" == start ]] || exit 2
host="$(jq -r '.target_host' <<<"$CONFIG_JSON")"
. "$(dirname "${BASH_SOURCE[0]}")/cmdb-ssh-login.sh"
selfhost_cmdb_ssh_login "${CMDB_FILE:-cmdb/cmdb.json}" "$host" "$REQUESTED_ENVIRONMENT"
remote=("${sudo_prefix[@]}" docker "$action" web-saas-accounts web-saas-billing)
printf -v command '%q ' "${remote[@]}"
ssh -i "$HOME/.ssh/id_deploy" -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new "${ssh_user}@${host_ip}" "$command" >/dev/null
echo "application_services=${action}"
