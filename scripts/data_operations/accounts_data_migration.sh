#!/bin/bash
set -euo pipefail

# ==============================================================================
# Accounts Data Migration Script (PROD -> UAT)
# Safe, 4-layer Anti-Foolishness Enforced One-Way Migration Execution
#
# Implements chapter 4 of
#   docs/data_migration/accounts_prod_to_uat_migration_guide.md
#     export -> dry-run preview -> merge import -> convergence verification
#
# Env:
#   MIGRATION_SOURCE_DSN  PROD read-only DSN (required)
#   MIGRATION_TARGET_DSN  UAT DSN (required, asserted below)
#   MIGRATECTL_BIN        migratectl binary (default: migratectl)
#   SNAPSHOT_FILE         snapshot path (default: /tmp/account-prod-snapshot.yaml)
#   DRY_RUN               "true" = stop after the preview, never write (default false)
#   SKIP_VERIFY           "true" = skip the post-import convergence check
#
# Verified by:
#   .github/scripts/tests/accounts_data_migration_safeguard_test.sh  (no DB)
#   .github/scripts/tests/accounts_data_migration_e2e.sh             (docker + postgres)
# ==============================================================================

SOURCE_DSN="${MIGRATION_SOURCE_DSN:-}"
TARGET_DSN="${MIGRATION_TARGET_DSN:-}"
MIGRATECTL_BIN="${MIGRATECTL_BIN:-migratectl}"
SNAPSHOT_FILE="${SNAPSHOT_FILE:-/tmp/account-prod-snapshot.yaml}"
DRY_RUN="${DRY_RUN:-false}"
SKIP_VERIFY="${SKIP_VERIFY:-false}"

# The snapshot contains password hashes and session tokens: never leave it on
# the runner, not even when a later step fails.
cleanup_snapshot() {
  rm -f "${SNAPSHOT_FILE}"
}
trap cleanup_snapshot EXIT

# Strip credentials before echoing any DSN.
redact_dsn() {
  printf '%s' "$1" | sed -E 's#(://[^:/@]+):[^@]*@#\1:***@#'
}

if [ -z "${SOURCE_DSN}" ] || [ -z "${TARGET_DSN}" ]; then
  echo "ERROR: MIGRATION_SOURCE_DSN and MIGRATION_TARGET_DSN environment variables are required." >&2
  exit 1
fi

echo "=========================================="
echo " Starting Accounts One-Way Data Migration "
echo "=========================================="
echo "  source : $(redact_dsn "${SOURCE_DSN}")"
echo "  target : $(redact_dsn "${TARGET_DSN}")"
echo "  dry-run: ${DRY_RUN}"

# ------------------------------------------------------------------------------
# Safeguard Assertions (防呆绝杀校验)
# ------------------------------------------------------------------------------
echo "[SAFEGUARD-CHECK] Validating DSN direction rules..."

# Rule 1: Target DSN MUST NOT contain PROD domains (svc.plus / console.svc.plus)
if [[ "${TARGET_DSN}" =~ "svc.plus" ]]; then
  echo "[CRITICAL ERROR] Target DSN contains PROD domain (svc.plus)! Aborting immediately to prevent writing to PROD!" >&2
  exit 1
fi

# Rule 2: Target DSN MUST contain valid non-prod / UAT domain (onwalk.net or localhost/test)
if [[ ! "${TARGET_DSN}" =~ "onwalk.net" ]] && [[ ! "${TARGET_DSN}" =~ "127.0.0.1" ]] && [[ ! "${TARGET_DSN}" =~ "localhost" ]]; then
  echo "[CRITICAL ERROR] Target DSN domain is not recognized as UAT (expected *.onwalk.net). Aborting for safety!" >&2
  exit 1
fi

# Rule 3: Source and target must not be the same endpoint (a swapped/duplicated
# secret would otherwise merge an environment into itself).
if [ "${SOURCE_DSN}" = "${TARGET_DSN}" ]; then
  echo "[CRITICAL ERROR] Source and target DSN are identical! Aborting." >&2
  exit 1
fi

echo "[SAFEGUARD-CHECK] Target DSN passed safety assertions. Target is verified as UAT environment."

# ------------------------------------------------------------------------------
# Step 1: Export Snapshot from PROD (Read-Only)
# ------------------------------------------------------------------------------
echo "[STEP 1/4] Exporting user data snapshot from PROD DB..."
${MIGRATECTL_BIN} export \
  --dsn "${SOURCE_DSN}" \
  --output "${SNAPSHOT_FILE}"

echo "[STEP 1/4] Snapshot successfully generated at ${SNAPSHOT_FILE}."

# ------------------------------------------------------------------------------
# Step 2: Dry-Run Import & Merge Preview on UAT
# ------------------------------------------------------------------------------
echo "[STEP 2/4] Running Dry-Run import preview on UAT target DB..."
${MIGRATECTL_BIN} import \
  --dsn "${TARGET_DSN}" \
  --file "${SNAPSHOT_FILE}" \
  --regenerate-user-uuids \
  --dry-run \
  --merge \
  --merge-strategy timestamp

echo "[STEP 2/4] Dry-Run completed successfully."

# ------------------------------------------------------------------------------
# Step 3: Apply Merge (if not dry run only mode)
# ------------------------------------------------------------------------------
if [ "${DRY_RUN}" = "true" ]; then
  echo "[STEP 3/4] DRY_RUN=true is enabled. Skipping actual database write."
  echo "[STEP 4/4] Skipping post-import verification (nothing was written)."
  echo "=========================================="
  echo " Dry-Run Completed Cleanly                "
  echo "=========================================="
  exit 0
fi

echo "[STEP 3/4] Executing real merge import into UAT target DB..."
${MIGRATECTL_BIN} import \
  --dsn "${TARGET_DSN}" \
  --file "${SNAPSHOT_FILE}" \
  --regenerate-user-uuids \
  --merge \
  --merge-strategy timestamp
echo "[STEP 3/4] Accounts data migration completed successfully."

# ------------------------------------------------------------------------------
# Step 4: Post-import convergence verification
#
# Replaying the same snapshot as a dry-run must now be a no-op: any remaining
# insert means rows from PROD did not land in UAT.
# ------------------------------------------------------------------------------
if [ "${SKIP_VERIFY}" = "true" ]; then
  echo "[STEP 4/4] SKIP_VERIFY=true. Skipping post-import verification."
else
  echo "[STEP 4/4] Verifying convergence (replaying snapshot as dry-run)..."
  VERIFY_OUTPUT="$(${MIGRATECTL_BIN} import \
    --dsn "${TARGET_DSN}" \
    --file "${SNAPSHOT_FILE}" \
    --regenerate-user-uuids \
    --dry-run \
    --merge \
    --merge-strategy timestamp 2>&1)"
  echo "${VERIFY_OUTPUT}"

  if ! grep -qE 'users inserted=0 updated=0' <<<"${VERIFY_OUTPUT}" ||
     ! grep -qE 'Identities inserted=0 updated=0' <<<"${VERIFY_OUTPUT}" ||
     ! grep -qE 'Sessions inserted=0 updated=0' <<<"${VERIFY_OUTPUT}"; then
    echo "[VERIFY FAILED] Snapshot rows are still missing in UAT after the merge." >&2
    exit 1
  fi
  echo "[STEP 4/4] Verification passed: UAT already contains every snapshot row."
fi

echo "=========================================="
echo " Migration Completed Cleanly              "
echo "=========================================="
