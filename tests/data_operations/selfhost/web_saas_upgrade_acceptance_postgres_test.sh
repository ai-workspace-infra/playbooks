#!/usr/bin/env bash
set -euo pipefail

# Uses only a disposable PostgreSQL on loopback; schema/data mutations are confined to database `account`.
script="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)/scripts/data_operations/selfhost/acceptance.sh"
export PGHOST="${PGHOST:-127.0.0.1}" PGPORT="${PGPORT:-5432}" PGUSER="${PGUSER:-postgres}"
case "$PGHOST" in 127.0.0.1|localhost|::1) ;; *) echo "refusing non-loopback PGHOST=$PGHOST" >&2; exit 2 ;; esac
psql -XAtq -d postgres -c 'SELECT 1' >/dev/null || { echo 'loopback PostgreSQL is unavailable' >&2; exit 2; }
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin"
printf '{"environment":"uat","web-saas-uat":{"ip":"127.0.0.1","ansible_user":"ubuntu","groups":["web_saas"]}}\n' >"$tmp/cmdb.json"
cat >"$tmp/bin/docker" <<'DOCKER'
#!/usr/bin/env bash
set -euo pipefail
case "$1" in
  inspect)
    case "$3" in
      *State.Status*) echo running ;;
      *Config.Image*) echo "fixture:$FAKE_TAG" ;;
      *NetworkSettings.Networks*) echo 127.0.0.1 ;;
      *) exit 1 ;;
    esac ;;
  exec)
    shift
    while [ "$1" != web-saas-postgresql ]; do
      case "$1" in -i) shift ;; -e) export "$2"; shift 2 ;; *) echo "unexpected docker arg $1" >&2; exit 2 ;; esac
    done
    shift
    [ "$1" = psql ] || exit 2
    shift
    exec psql -h "$PGHOST" -p "$PGPORT" "$@" ;;
  *) exit 1 ;;
esac
DOCKER
cat >"$tmp/bin/ssh" <<'SSH'
#!/usr/bin/env bash
set -euo pipefail
cmd="${!#}"
read -r -a args <<<"${cmd#sudo -n }"
[ "${args[0]}" = bash ] && [ "${args[1]}" = -s ] || exit 2
shifted=("${args[@]:3}")
sed "s#/var/lib/platform-ops/upgrade-acceptance#$FAKE_STATE_ROOT#g" | bash -s -- "${shifted[@]}"
SSH
cat >"$tmp/bin/timeout" <<'TIMEOUT'
#!/usr/bin/env bash
echo "HTTP/1.0 ${HTTP_STATUS:-200} Fixture"
TIMEOUT
chmod +x "$tmp/bin/"*

psql -XAtq -d postgres -c 'DROP DATABASE IF EXISTS account' >/dev/null
psql -XAtq -d postgres -c 'CREATE DATABASE account' >/dev/null
psql -XAtq -v ON_ERROR_STOP=1 -d account <<'SQL'
CREATE TABLE schema_migrations(version bigint NOT NULL, dirty boolean NOT NULL);
INSERT INTO schema_migrations VALUES (10, false);
CREATE TABLE users(uuid uuid PRIMARY KEY, username text, password text, updated_at timestamptz);
CREATE TABLE identities(uuid uuid PRIMARY KEY, provider text, external_id text, updated_at timestamptz);
CREATE TABLE subscriptions(uuid uuid PRIMARY KEY, provider text, external_id text, status text, updated_at timestamptz);
INSERT INTO users VALUES ('11111111-1111-1111-1111-111111111111','alice','hash',now());
INSERT INTO identities VALUES ('22222222-2222-2222-2222-222222222222','oidc','private',now());
INSERT INTO subscriptions VALUES ('33333333-3333-3333-3333-333333333333','stripe','private','active',now());
SQL

run() {
  local fake_tag="${FAKE_TAG:-uat-daily-build-2026.10.04-r1}"
  PATH="$tmp/bin:$PATH" FAKE_STATE_ROOT="$tmp/state" FAKE_TAG="$fake_tag" HTTP_STATUS="${HTTP_STATUS:-200}" \
    REQUESTED_ENVIRONMENT=uat RELEASE_TAG="${RELEASE_TAG_OVERRIDE:-uat-daily-build-2026.10.04-r1}" EXPECTED_SCHEMA_VERSION="${EXPECTED_SCHEMA_VERSION_OVERRIDE:-11}" \
    CONFIG_JSON="{\"target_host\":\"web-saas-uat\",\"acceptance_run_id\":\"${ACCEPTANCE_ID:-integration-1}\"}" \
    CMDB_FILE="$tmp/cmdb.json" HOME="$tmp" bash "$script" "$@"
}
pass=0
expect() { local name="$1"; shift; if "$@"; then ((pass+=1)); echo "[PASS] $name"; else echo "[FAIL] $name" >&2; exit 1; fi; }
run baseline >"$tmp/baseline.out"
expect 'baseline captures subscription count and migration state' grep -q '^rows_subscriptions=1$' "$tmp/baseline.out"
expect 'baseline receipt output never contains row values or identifiers' bash -c '! grep -Eq "alice|private|hash|11111111" "$1"' _ "$tmp/baseline.out"
psql -XAtq -v ON_ERROR_STOP=1 -d account -c "UPDATE schema_migrations SET version=11; UPDATE users SET updated_at=now()+interval '1 hour'; INSERT INTO users VALUES ('44444444-4444-4444-4444-444444444444','new','new',now());" >/dev/null
run verify >"$tmp/verify.out"
expect 'lossless version advance, volatile churn, and new signup pass' grep -q '^preserved_users=1$' "$tmp/verify.out"
expect 'real baseline evidence is used for verification' grep -q '^baseline_preserved=true$' "$tmp/verify.out"
if FAKE_TAG=uat-daily-build-2026.10.03-r1 run verify >"$tmp/image.out" 2>&1; then echo 'stale image tag unexpectedly passed' >&2; exit 1; fi
expect 'an Accounts/Console image on the old release tag fails' grep -q 'image tag does not match release_tag' "$tmp/image.out"
if HTTP_STATUS=503 run verify >"$tmp/http.out" 2>&1; then echo 'HTTP readiness failure unexpectedly passed' >&2; exit 1; fi
expect 'Accounts/Console HTTP readiness failure is rejected' grep -q 'selfhost HTTP readiness probe failed' "$tmp/http.out"
if EXPECTED_SCHEMA_VERSION_OVERRIDE=12 run verify >"$tmp/version.out" 2>&1; then echo 'wrong schema version unexpectedly passed' >&2; exit 1; fi
expect 'schema version must equal the immutable source target' grep -q 'schema version differs from expected_schema_version' "$tmp/version.out"
psql -XAtq -d account -c 'UPDATE schema_migrations SET dirty=true' >/dev/null
if run verify >"$tmp/dirty.out" 2>&1; then echo 'dirty migration unexpectedly passed' >&2; exit 1; fi
expect 'dirty migration state fails' grep -q 'schema migration is dirty' "$tmp/dirty.out"
psql -XAtq -d account -c 'UPDATE schema_migrations SET dirty=false, version=11' >/dev/null
psql -XAtq -d account -c "UPDATE subscriptions SET status='cancelled'" >/dev/null
if run verify >"$tmp/changed.out" 2>&1; then echo 'changed subscription unexpectedly passed' >&2; exit 1; fi
expect 'changed subscription business data fails fingerprint comparison' grep -q 'subscriptions records changed or disappeared' "$tmp/changed.out"
if ACCEPTANCE_ID=missing-baseline run verify >"$tmp/no-baseline.out" 2>&1; then echo 'verify without baseline unexpectedly passed' >&2; exit 1; fi
expect 'verification without a real prior baseline fails' grep -q 'No pre-upgrade baseline' "$tmp/no-baseline.out"

# Empty subscription samples are not accepted as proof of preservation.
if ACCEPTANCE_ID=empty-sample run baseline >"$tmp/empty-baseline.out" 2>&1; then :; fi
psql -XAtq -d account -c 'DELETE FROM subscriptions' >/dev/null
if ACCEPTANCE_ID=empty-sample run verify >"$tmp/empty-verify.out" 2>&1; then echo 'empty baseline sample unexpectedly passed' >&2; exit 1; fi
expect 'empty baseline subscription sample fails closed' grep -q 'no subscription sample' "$tmp/empty-verify.out"

echo "web_saas_upgrade_acceptance_postgres_test: $pass PostgreSQL 17 acceptance checks passed"
