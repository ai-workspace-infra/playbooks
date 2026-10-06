#!/usr/bin/env bash
# Disposable GitHub-hosted runner only: reproduce nested PGDATA bind permissions.
set -euo pipefail
[[ "${GITHUB_ACTIONS:-}" == true && "${RUNNER_ENVIRONMENT:-}" == github-hosted ]]
: "${RUNNER_TEMP:?disposable runner directory required}"
fixture_dir="$(mktemp -d "$RUNNER_TEMP/native-bind-permissions.XXXXXX")"
fixture_name="native-bind-permissions-${GITHUB_RUN_ID:?}-${GITHUB_RUN_ATTEMPT:?}"
trap 'docker rm --force "$fixture_name" >/dev/null 2>&1 || true; sudo rm -rf -- "$fixture_dir"' EXIT
image=postgres:17
sudo install -d -m 700 -o 0 -g 0 "$fixture_dir/data"
docker run -d --name "$fixture_name" --restart no --network none \
  -e POSTGRES_PASSWORD=fictional-disposable-only -e PGDATA=/var/lib/postgresql/data/pgdata \
  --mount "type=bind,src=$fixture_dir/data,dst=/var/lib/postgresql/data" "$image" >/dev/null
for _ in {1..30}; do
  [[ "$(docker inspect -f '{{.State.Running}}' "$fixture_name")" == false ]] && break
  sleep 1
done
[[ "$(docker inspect -f '{{.State.Running}}' "$fixture_name")" == false ]]
docker logs "$fixture_name" > "$fixture_dir/failure.private.log" 2>&1
rg -qi 'permission denied' "$fixture_dir/failure.private.log"
uid="$(docker run --rm --network none --entrypoint id "$image" -u postgres)"
gid="$(docker run --rm --network none --entrypoint id "$image" -g postgres)"
[[ "$uid" =~ ^[1-9][0-9]{0,5}$ && "$gid" =~ ^[1-9][0-9]{0,5}$ ]]
sudo chown "$uid:$gid" "$fixture_dir/data"
sudo chmod 700 "$fixture_dir/data"
docker start "$fixture_name" >/dev/null
ready=false
for _ in {1..30}; do
  if docker exec "$fixture_name" pg_isready -U postgres -h 127.0.0.1 >/dev/null 2>&1; then ready=true; break; fi
  sleep 2
done
[[ "$ready" == true ]]
version="$(docker exec "$fixture_name" psql -U postgres -d postgres -XAtq -c 'SHOW server_version_num')"
[[ "$version" =~ ^17[0-9]{4}$ ]]
[[ "$(docker exec "$fixture_name" psql -U postgres -d postgres -XAtq -c "SELECT count(*) FROM pg_database WHERE datname='account'")" == 0 ]]
echo 'Disposable PostgreSQL 17 reproduced root-owned nested bind failure and recovered by parent-only image UID/GID correction; no business DB initialized.'
