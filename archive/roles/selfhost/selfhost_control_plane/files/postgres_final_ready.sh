#!/bin/sh
# Read-only probe inside the PostgreSQL container. The official entrypoint's
# temporary initialization server is not PID 1 and must never release this gate.
set -eu
test "$(cat /proc/1/comm)" = postgres
pg_isready -h 127.0.0.1 -U postgres -d postgres >/dev/null 2>&1
test "$(psql -U postgres -d postgres -XAtq -v ON_ERROR_STOP=1 -c 'SELECT 1' 2>/dev/null)" = 1
