#!/bin/bash
set -eo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/../lib/require-env.sh"

# Fail red when the Vault KV keys are missing, and assert the two DSNs point at
# the environments they are supposed to. Without this the migration script would
# still refuse to write to PROD, but a mis-keyed secret would surface as an
# opaque connection error rather than as "the secret is not there".

require_env MIGRATION_SOURCE_DSN MIGRATION_TARGET_DSN

# Source must be PROD. A UAT-to-UAT run would silently produce a green pipeline
# that migrated nothing of value.
if [[ "${MIGRATION_SOURCE_DSN}" != *"svc.plus"* ]]; then
  echo "::error::MIGRATION_SOURCE_DSN does not point at a PROD host (*.svc.plus)." >&2
  exit 1
fi

# Target must be UAT. The migration script asserts this again; duplicating it
# here means a bad secret fails before any database is contacted at all.
if [[ "${MIGRATION_TARGET_DSN}" == *"svc.plus"* ]]; then
  echo "::error::MIGRATION_TARGET_DSN points at PROD (svc.plus). Refusing to continue." >&2
  exit 1
fi
if [[ "${MIGRATION_TARGET_DSN}" != *"onwalk.net"* ]]; then
  echo "::error::MIGRATION_TARGET_DSN is not a recognised UAT host (*.onwalk.net)." >&2
  exit 1
fi

# The PROD credential handed to this pipeline must be the SELECT-only role
# (safeguard layer 4). Catching the username here is cheap; the database-side
# grant remains the real enforcement.
if [[ "${MIGRATION_SOURCE_DSN}" != *"://readonly:"* ]]; then
  echo "::error::MIGRATION_SOURCE_DSN does not use the PROD 'readonly' role." >&2
  echo "  The migration pipeline must never hold a write-capable PROD credential." >&2
  exit 1
fi

echo "Migration credentials present: source=PROD(readonly), target=UAT."
