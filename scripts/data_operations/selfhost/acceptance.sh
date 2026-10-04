#!/usr/bin/env bash
set -euo pipefail

action="${1:-}"
[[ "${action}" == baseline || "${action}" == probe || "${action}" == verify ]] || {
  echo "usage: acceptance.sh baseline|probe|verify" >&2
  exit 2
}

config="${CONFIG_JSON:-}"
[[ -n "$config" ]] || config='{}'
host="$(jq -r '.target_host // empty' <<<"${config}")"
cmdb_file="${CMDB_FILE:-cmdb/cmdb.json}"
environment="${REQUESTED_ENVIRONMENT:-}"
run_id="$(jq -r '.acceptance_run_id // empty' <<<"${config}")"
release_tag="${RELEASE_TAG:-}"
expected_version="${EXPECTED_SCHEMA_VERSION:-$(jq -r '.expected_schema_version // empty' <<<"${config}")}"
[[ "${environment}" =~ ^(uat|prod|sit)$ ]] || { echo "invalid environment" >&2; exit 2; }
[[ "${run_id}" =~ ^[A-Za-z0-9._-]{1,100}$ ]] || { echo "invalid acceptance_run_id" >&2; exit 2; }
[[ "${action}" != verify || "${release_tag}" =~ ^[A-Za-z0-9._-]{1,100}$ ]] || { echo "release_tag required" >&2; exit 2; }
. "$(dirname "${BASH_SOURCE[0]}")/cmdb-ssh-login.sh"
selfhost_cmdb_ssh_login "${cmdb_file}" "${host}" "${environment}"

ssh_opts=(-i "$HOME/.ssh/id_deploy" -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new)
remote_command=("${sudo_prefix[@]}" bash -s -- "$action" "$run_id" "$release_tag" "$expected_version")
ssh "${ssh_opts[@]}" "${ssh_user}@${host_ip}" "${remote_command[*]}" <<'REMOTE'
set -euo pipefail
action="$1"; run_id="$2"; release_tag="$3"; expected_version="$4"
root=/var/lib/platform-ops/upgrade-acceptance
dir="${root}/${run_id}"
pg=web-saas-postgresql
accounts=web-saas-accounts
console=web-saas-console
q() { docker exec -i -e PGTZ=UTC "$pg" psql -U postgres -d account -XAtq -v ON_ERROR_STOP=1 -c "$1"; }
db_present() {
  [ "$(docker inspect -f '{{.State.Status}}' "$pg" 2>/dev/null || true)" = running ] || return 1
  [ "$(docker exec "$pg" psql -U postgres -d postgres -XAtq -c "SELECT 1 FROM pg_database WHERE datname='account'" 2>/dev/null || true)" = 1 ]
}
row_fingerprints() {
  local table="$1"; local excluded
  case "$table" in
    users) excluded="ARRAY['updated_at','last_active_at','version','session_token','last_login_at']" ;;
    identities) excluded="ARRAY['updated_at','last_seen_at']" ;;
    subscriptions) excluded="ARRAY['updated_at','last_checked_at']" ;;
    *) return 2 ;;
  esac
  if [ "$(q "SELECT to_regclass('public.${table}') IS NOT NULL")" != t ]; then
    printf 'absent\n'; return 0
  fi
  q "SELECT uuid::text || ' ' || md5((to_jsonb(t) - ${excluded})::text) FROM public.${table} AS t ORDER BY uuid"
}
http_status() {
  local container="$1" port="$2" path="$3" ip status
  ip="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' "$container" 2>/dev/null | awk '{print $1}')"
  [ -n "$ip" ] || { echo none; return; }
  status="$(timeout 10 bash -c 'exec 3<>"/dev/tcp/$0/$1" && printf "GET %s HTTP/1.0\r\nHost: localhost\r\nConnection: close\r\n\r\n" "$2" >&3 && head -n1 <&3' "$ip" "$port" "$path" 2>/dev/null | awk '{print $2}' | tr -d '\r' || true)"
  echo "${status:-none}"
}

case "$action" in
  baseline)
    install -d -m 700 "$root"
    if [ -s "$dir/users.rows" ] || [ -s "$dir/summary" ]; then
      echo "baseline=already-captured"; cat "$dir/summary"; exit 0
    fi
    tmp="$(mktemp -d "$root/.capture.XXXXXX")"
    chmod 700 "$tmp"
    if db_present; then
      {
        echo state=present
        echo "migration=$(q "SELECT version::text || ':' || dirty::text FROM public.schema_migrations LIMIT 1" 2>/dev/null || echo absent)"
        for table in users identities subscriptions; do
          row_fingerprints "$table" >"$tmp/$table.rows"
          echo "rows_${table}=$(wc -l <"$tmp/$table.rows" | tr -d ' ')"
        done
      } >"$tmp/summary"
    else
      echo state=absent >"$tmp/summary"
    fi
    chmod -R go-rwx "$tmp"
    rm -rf "$dir"
    mv "$tmp" "$dir"
    echo baseline=captured
    cat "$dir/summary"
    ;;
  probe)
    db_present || { echo "PostgreSQL account database is not ready" >&2; exit 1; }
    docker inspect "$accounts" >/dev/null
    docker inspect "$console" >/dev/null
    echo "postgres=ready"
    readyz="$(http_status "$accounts" 8080 /readyz)"
    ping="$(http_status "$accounts" 8080 /api/ping)"
    console_status="$(http_status "$console" 3000 /)"
    echo "accounts_readyz=${readyz}"
    echo "accounts_ping=${ping}"
    echo "console_root=${console_status}"
    for status in "$readyz" "$ping" "$console_status"; do
      [[ "$status" =~ ^[23][0-9][0-9]$ ]] || { echo "selfhost HTTP readiness probe failed" >&2; exit 1; }
    done
    ;;
  verify)
    [ -s "$dir/summary" ] || { echo "No pre-upgrade baseline for this run" >&2; exit 1; }
    grep -qx 'state=present' "$dir/summary" || { echo "baseline did not capture an existing account database" >&2; exit 1; }
    subscription_count="$(sed -n 's/^rows_subscriptions=//p' "$dir/summary")"
    [[ "$subscription_count" =~ ^[0-9]+$ ]] && [ "$subscription_count" -gt 0 ] || { echo "baseline has no subscription sample to preserve" >&2; exit 1; }
    db_present || { echo "PostgreSQL account database is not ready" >&2; exit 1; }
    for container in "$accounts" "$console"; do
      [ "$(docker inspect -f '{{.State.Status}}' "$container")" = running ] || { echo "$container is not running" >&2; exit 1; }
      image="$(docker inspect -f '{{.Config.Image}}' "$container")"
      [[ "$image" == *":${release_tag}" ]] || { echo "$container image tag does not match release_tag" >&2; exit 1; }
    done
    readyz="$(http_status "$accounts" 8080 /readyz)"
    ping="$(http_status "$accounts" 8080 /api/ping)"
    console_status="$(http_status "$console" 3000 /)"
    for status in "$readyz" "$ping" "$console_status"; do
      [[ "$status" =~ ^[23][0-9][0-9]$ ]] || { echo "selfhost HTTP readiness probe failed" >&2; exit 1; }
    done
    migration="$(q "SELECT version::text || ':' || dirty::text FROM public.schema_migrations LIMIT 1")"
    [[ "$migration" != *:true ]] || { echo "schema migration is dirty" >&2; exit 1; }
    if [ -n "$expected_version" ]; then
      [[ "$migration" == "${expected_version}:false" ]] || { echo "schema version differs from expected_schema_version" >&2; exit 1; }
    fi
    for table in users identities subscriptions; do
      [ -f "$dir/$table.rows" ] || continue
      row_fingerprints "$table" >"$dir/$table.after"
      chmod 600 "$dir/$table.after"
      missing="$(LC_ALL=C join -v1 "$dir/$table.rows" "$dir/$table.after" | wc -l | tr -d ' ')"
      changed="$(LC_ALL=C join "$dir/$table.rows" "$dir/$table.after" | awk '$2 != $3 {n++} END {print n+0}')"
      [ "$missing" = 0 ] && [ "$changed" = 0 ] || { echo "pre-upgrade ${table} records changed or disappeared (missing=${missing}, changed=${changed})" >&2; exit 1; }
      echo "preserved_${table}=$(wc -l <"$dir/$table.rows" | tr -d ' ')"
    done
    echo "release_tag=${release_tag}"
    echo "migration=${migration}"
    echo "baseline_preserved=true"
    ;;
esac
REMOTE
