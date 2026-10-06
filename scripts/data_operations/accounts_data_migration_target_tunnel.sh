#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bash "$root/data-migration_accounts_assert-credentials.sh"
. "$root/selfhost/cmdb-ssh-login.sh"
selfhost_cmdb_ssh_login "$CMDB_FILE" "$MIGRATION_TARGET_HOST" uat
opts=(-i "$MIGRATION_SSH_KEY_PATH" -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new)
printf -v command '%q ' "${sudo_prefix[@]}" docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' web-saas-postgresql
remote_ip="$(ssh "${opts[@]}" "$ssh_user@$host_ip" "$command" | awk '{print $1}')"
[[ "$remote_ip" =~ ^[0-9.]+$ ]] || { echo 'Invalid target database container address' >&2; exit 1; }
control="$(mktemp -d)"
cleanup() { ssh "${opts[@]}" -S "$control/socket" -O exit "$ssh_user@$host_ip" >/dev/null 2>&1 || true; rm -rf "$control"; }
trap cleanup EXIT
trap 'exit 143' TERM INT
ssh "${opts[@]}" -M -S "$control/socket" -fN -o ExitOnForwardFailure=yes -L "127.0.0.1:15432:$remote_ip:5432" "$ssh_user@$host_ip" > /dev/null 2> "$control/ssh.stderr"
MIGRATION_TARGET_DSN="$(python3 - <<'DSN'
import os
from urllib.parse import urlsplit, urlunsplit
p = urlsplit(os.environ['MIGRATION_TARGET_DSN'])
credentials = p.netloc.rsplit('@', 1)[0]
print(urlunsplit((p.scheme, credentials + '@127.0.0.1:15432', p.path, p.query, p.fragment)))
DSN
)"
export MIGRATION_TARGET_DSN
bash "$root/accounts_data_migration.sh"
