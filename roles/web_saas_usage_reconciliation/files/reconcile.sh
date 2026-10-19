#!/usr/bin/env bash
set -euo pipefail
umask 077
IFS= read -r MIGRATION_SOURCE_DSN
[[ -n "$MIGRATION_SOURCE_DSN" ]]
export MIGRATION_SOURCE_DSN
[[ ${MERGE_RUN:-} =~ ^[1-9][0-9]*$ ]]
[[ ${MERGE_BINARY_SHA256:-} =~ ^[0-9a-f]{64}$ ]]
[[ ${MERGE_SCHEMA_SHA256:-} =~ ^[0-9a-f]{64}$ ]]
[[ ${MERGE_BILLING_SHA256:-} =~ ^[0-9a-f]{64}$ ]]
[[ ${MERGE_VERSION:-} =~ ^[1-9][0-9]*$ ]]
[[ ${MERGE_APPLY:-} == true || ${MERGE_APPLY:-} == false ]]
[[ ${MERGE_EXECUTION_MODE:-container} == container || ${MERGE_EXECUTION_MODE:-container} == host-loopback ]]
[[ -f "$MERGE_BINARY" && ! -L "$MERGE_BINARY" ]]
[[ $(sha256sum "$MERGE_BINARY" | cut -d ' ' -f1) == "$MERGE_BINARY_SHA256" ]]
stage=$(mktemp -d /tmp/usage-reconcile.XXXXXXXX)
container_path="/tmp/usage-reconcile-${stage##*.}"
cleanup() {
  if [[ ${MERGE_EXECUTION_MODE:-container} == container ]]; then
    docker exec --user root web-saas-postgresql rm -f "$container_path" >/dev/null 2>&1 || true
  fi
  rm -f "$stage/preview.json" "$stage/result.json"
  rmdir "$stage"
}
trap cleanup EXIT
if [[ ${MERGE_EXECUTION_MODE:-container} == container ]]; then
  docker cp "$MERGE_BINARY" "web-saas-postgresql:$container_path" >/dev/null
  docker exec --user root web-saas-postgresql chmod 0555 "$container_path"
  [[ $(docker exec web-saas-postgresql sha256sum "$container_path" | cut -d ' ' -f1) == "$MERGE_BINARY_SHA256" ]]
  export MIGRATION_TARGET_DSN='host=/var/run/postgresql user=postgres dbname=account sslmode=disable'
else
  # Source is an existing authenticated SSH reverse forward, never a public DB port.
  python3 - <<'PY'
import os, urllib.parse
u = urllib.parse.urlsplit(os.environ['MIGRATION_SOURCE_DSN'])
assert u.scheme in ('postgres', 'postgresql') and u.hostname == '127.0.0.1'
assert u.port and u.path == '/account'
PY
  MIGRATION_TARGET_DSN=$(python3 - <<'PY'
import ipaddress, json, subprocess, urllib.parse
c, = json.loads(subprocess.check_output(['docker', 'inspect', 'web-saas-postgresql']))
assert c['Name'] == '/web-saas-postgresql' and c['State']['Running']
assert any(m['Type'] == 'bind' and m['Source'] == '/data/postgresql' for m in c['Mounts'])
network, = c['NetworkSettings']['Networks'].values()
ip = network['IPAddress']
assert ipaddress.ip_address(ip).is_private
env = dict(v.split('=', 1) for v in c['Config']['Env'] if '=' in v)
assert env['POSTGRES_USER'] == 'postgres' and env['POSTGRES_PASSWORD']
password = urllib.parse.quote(env['POSTGRES_PASSWORD'], safe='')
print(f'postgresql://postgres:{password}@{ip}:5432/account?sslmode=disable')
PY
)
  export MIGRATION_TARGET_DSN
fi
args=(reconcile-usage --source-dsn-env MIGRATION_SOURCE_DSN --target-dsn-env MIGRATION_TARGET_DSN
  --source-generation prod-v2 --target-generation prod-v3
  --schema-sha256 "$MERGE_SCHEMA_SHA256" --billing-schema-sha256 "$MERGE_BILLING_SHA256"
  --expected-target-version "$MERGE_VERSION")
invoke() {
  if [[ ${MERGE_EXECUTION_MODE:-container} == host-loopback ]]; then
    "$MERGE_BINARY" "${args[@]}" "$@"
    return
  fi
  docker exec --user postgres --env MIGRATION_SOURCE_DSN --env MIGRATION_TARGET_DSN \
    web-saas-postgresql "$container_path" "${args[@]}" "$@"
}
invoke > "$stage/preview.json"
plan=$(python3 - "$stage/preview.json" <<'PY'
import json, re, sys
p = json.load(open(sys.argv[1]))
assert p['result'] == 'preview' and p['conflicts'] == 0
assert p['source_read_only'] is True and p['target_writes'] is False
assert re.fullmatch('[0-9a-f]{64}', p['plan_sha256'])
print(p['plan_sha256'])
PY
)
if [[ "$MERGE_APPLY" == true ]]; then
  invoke --apply --writers-paused --expected-plan-sha256 "$plan" > "$stage/result.json"
  python3 - "$stage/result.json" <<'PY'
import json, sys
p = json.load(open(sys.argv[1]))
assert p['result'] == 'merged' and p['conflicts'] == 0
assert p['schema_before_sha256'] == p['schema_after_sha256']
assert p['users_before'] == p['users_after'] and p['source_read_only'] is True
print(json.dumps(p, sort_keys=True))
PY
else
  python3 -m json.tool "$stage/preview.json"
fi
