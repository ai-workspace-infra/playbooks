#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
checkpoint_script="${root}/scripts/data_operations/database/create_release_checkpoint.sh"
workflow="${root}/.github/workflows/serverless-database-operations.yml"
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
mkdir -p "${tmp}/bin"
printf '%s\n' '#!/usr/bin/env bash' 'touch "${PSQL_CALLED}"' >"${tmp}/bin/psql"
chmod +x "${tmp}/bin/psql"

if output="$(env PATH="${tmp}/bin:${PATH}" PSQL_CALLED="${tmp}/psql-called" \
  RELEASE_TAG=uat-daily-build-2026.09.23-r1 DATABASE_BACKEND=supabase DATABASE_ENV=uat \
  TARGET_DSN=postgres://user:secret@db.supabase.com/postgres REQUIRE_DURABLE_CHECKPOINT=true \
  bash "${checkpoint_script}" 2>&1)"; then
  echo 'Durable UAT checkpoint accepted missing remote backup credentials.' >&2
  exit 1
fi
[[ ! -e "${tmp}/psql-called" ]] || { echo 'Checkpoint attempted SQL before the remote backup preflight.' >&2; exit 1; }
[[ "${output}" == *'requires configured remote object storage credentials'* ]] || {
  echo 'Checkpoint did not explain the fail-closed storage preflight.' >&2
  exit 1
}
[[ "${output}" != *'secret'* && "${output}" != *'postgres://'* ]] || {
  echo 'Checkpoint preflight exposed the DSN.' >&2
  exit 1
}

if output="$(env PATH="${tmp}/success-bin:${PATH}" PSQL_CALLED="${tmp}/psql-called-missing-pass" \
  RELEASE_TAG=uat-daily-build-2026.09.23-r1 DATABASE_BACKEND=supabase DATABASE_ENV=uat \
  TARGET_DSN=postgres://user:secret@db.supabase.com/postgres S3_BUCKET=uat-checkpoints \
  AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_DEFAULT_REGION=uat-region \
  S3_REGION=uat-region S3_ENDPOINT=https://objects.uat.example.invalid S3_PREFIX=database-checkpoints \
  REQUIRE_DURABLE_CHECKPOINT=true CHECKPOINT_DIR="${tmp}/missing-pass" RUNNER_TEMP="${tmp}" \
  bash "${checkpoint_script}" 2>&1)"; then
  echo 'Durable UAT checkpoint accepted a missing UAT-only encryption key.' >&2
  exit 1
fi
[[ ! -e "${tmp}/psql-called-missing-pass" ]] || { echo 'Checkpoint issued SQL before the UAT encryption-key preflight.' >&2; exit 1; }
[[ "${output}" == *'BACKUP_ENCRYPTION_PASS from the UAT-only Vault field kv/data/uat/serverless/database-backup'* ]] || {
  echo 'Missing encryption-key failure did not name the exact UAT Vault field.' >&2
  exit 1
}

mkdir -p "${tmp}/success-bin"
printf '%s\n' '#!/usr/bin/env bash' \
  'case "$*" in' \
  '  *"server_version_num"*) printf "15\\n" ;;' \
  '  *"CREATE TABLE IF NOT EXISTS public.system_release_checkpoints"*) ;;' \
  '  *"INSERT INTO public.system_release_checkpoints"*) touch "${LEDGER_RECORDED}" ;;' \
  '  *) exit 4 ;;' \
  'esac' >"${tmp}/success-bin/psql"
printf '%s\n' '#!/usr/bin/env bash' \
  'if [[ "$1" == "--version" ]]; then printf "pg_dump (PostgreSQL) 15.0\\n"; exit 0; fi' \
  'for arg in "$@"; do [[ "$arg" == --file=* ]] && dump_file="${arg#--file=}"; done' \
  '[[ -n "${dump_file:-}" ]] || exit 5' \
  'printf "%s\\n" "-- private fixture row" >"${dump_file}"' >"${tmp}/success-bin/pg_dump"
printf '%s\n' '#!/usr/bin/env bash' \
  'if [[ "$1 $2" == "s3 cp" ]]; then' \
  '  [[ "$3" == *.enc && -s "$3" ]] || exit 6' \
  '  touch "${REMOTE_OBJECT_UPLOADED}"' \
  '  exit 0' \
  'fi' \
  'if [[ "$1 $2" == "s3api head-object" ]]; then' \
  '  [[ -e "${REMOTE_OBJECT_UPLOADED}" ]] || exit 7' \
  '  printf "17\\n"' \
  '  exit 0' \
  'fi' \
  'exit 8' >"${tmp}/success-bin/aws"
chmod +x "${tmp}/success-bin/psql" "${tmp}/success-bin/pg_dump" "${tmp}/success-bin/aws"
success_output="${tmp}/success-output"
if output="$(env PATH="${tmp}/success-bin:${PATH}" RELEASE_TAG=uat-daily-build-2026.09.23-r1 \
  DATABASE_BACKEND=supabase DATABASE_ENV=uat GIT_SHA=0123456789012345678901234567890123456789 \
  TARGET_DSN=postgres://user:secret@db.supabase.com/postgres S3_BUCKET=uat-checkpoints \
  AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_DEFAULT_REGION=uat-region \
  S3_REGION=uat-region S3_ENDPOINT=https://objects.uat.example.invalid S3_PREFIX=database-checkpoints \
  BACKUP_ENCRYPTION_PASS=test-encryption \
  REQUIRE_DURABLE_CHECKPOINT=true CHECKPOINT_DIR="${tmp}/checkpoint" RUNNER_TEMP="${tmp}" \
  GITHUB_OUTPUT="${success_output}" LEDGER_RECORDED="${tmp}/ledger-recorded" \
  REMOTE_OBJECT_UPLOADED="${tmp}/remote-object-uploaded" bash "${checkpoint_script}" 2>&1)"; then
  :
else
  echo "A valid encrypted remote checkpoint was rejected: ${output}" >&2
  exit 1
fi
[[ -e "${tmp}/ledger-recorded" && -e "${tmp}/remote-object-uploaded" ]] || {
  echo 'Successful checkpoint did not verify both remote object and ledger evidence.' >&2
  exit 1
}
[[ "$(<"${success_output}")" == 'durable_checkpoint_verified=true' ]] || {
  echo 'Successful checkpoint did not emit its non-sensitive verification result.' >&2
  exit 1
}
python3 - "${tmp}/checkpoint/manifest.json" <<'PY'
import json
import sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text())
if manifest['environment'] != 'uat' or not manifest['s3_uri'].startswith('s3://uat-checkpoints/database-checkpoints/uat/supabase/') or not manifest['s3_uri'].endswith('.sql.gz.enc'):
    raise SystemExit('Checkpoint manifest did not bind UAT to an encrypted remote object.')
PY
[[ "${output}" != *'secret'* && "${output}" != *'postgres://'* && "${output}" != *'private fixture row'* ]] || {
  echo 'Checkpoint logs exposed a DSN or private row fixture.' >&2
  exit 1
}

python3 - "${workflow}" <<'PY'
from pathlib import Path
import sys
import yaml

workflow = yaml.safe_load(Path(sys.argv[1]).read_text())
workflow_on = workflow.get('on', workflow.get(True, {}))
if set(workflow_on) != {'workflow_call'}:
    raise SystemExit('Serverless database operations must remain workflow_call only.')
jobs = workflow['jobs']
operation = jobs['database_operation']
steps = operation['steps']
checkpoint = next(step for step in steps if step.get('name') == 'Create release checkpoint')
if checkpoint['env'].get('REQUIRE_DURABLE_CHECKPOINT') != "${{ fromJSON(inputs.config_json).require_durable_checkpoint == true && 'true' || 'false' }}":
    raise SystemExit('Checkpoint operation must honor the caller durable-checkpoint config.')
uploads = [step for step in steps if step.get('uses', '').startswith('actions/upload-artifact')]
if len(uploads) != 1 or uploads[0]['with'].get('path') != '${{ runner.temp }}/uat-schema-repair-report.json':
    raise SystemExit('Only aggregate schema repair evidence may be uploaded; never upload checkpoint payload bytes.')
receipt = next(step for step in steps if step.get('name') == 'Verify trusted source run and sanitized isolated restore receipt')
vault = next(step for step in steps if step.get('name') == 'Load database credentials from Vault with GitHub OIDC')
if steps.index(receipt) > steps.index(vault):
    raise SystemExit('Checkpoint and /data restore evidence must fail closed before Vault credentials are loaded.')
if 'environment-data-operations.yml' not in (Path(sys.argv[1]).parent.parent.parent / 'scripts/data_operations/serverless/verify_checkpoint_receipt.py').read_text():
    raise SystemExit('Receipt verifier must trust the unified environment-data-operations workflow path.')
vault_secrets = next(step for step in steps if step.get('id') == 'vault_checkpoint')['with']['secrets']
required_vault_refs = (
    'kv/data/CICD/uat/iac_state TF_STATE_BUCKET | S3_BUCKET',
    'kv/data/CICD/uat/iac_state TF_STATE_ACCESS_KEY | AWS_ACCESS_KEY_ID',
    'kv/data/CICD/uat/iac_state TF_STATE_SECRET_KEY | AWS_SECRET_ACCESS_KEY',
    'kv/data/CICD/uat/iac_state TF_STATE_REGION | S3_REGION',
    'kv/data/CICD/uat/iac_state TF_STATE_ENDPOINT | S3_ENDPOINT',
    'kv/data/uat/serverless/database-backup BACKUP_ENCRYPTION_PASS | BACKUP_ENCRYPTION_PASS',
)
if any(ref not in vault_secrets for ref in required_vault_refs):
    raise SystemExit('Checkpoint must load only the documented UAT storage and encryption fields.')
if 'prod' in vault_secrets.lower() or next(step for step in steps if step.get('id') == 'vault_checkpoint')['with']['role'] != 'github-actions-platform-ops-toolkit-uat':
    raise SystemExit('Checkpoint Vault reads must use the UAT role and must not reference PROD.')
checkpoint_env = checkpoint['env']
if checkpoint_env.get('S3_PREFIX') != 'database-checkpoints':
    raise SystemExit('UAT checkpoint objects must use the isolated database-checkpoints/uat prefix.')
migration = next(step for step in steps if step.get('name') == 'Apply reviewed Accounts incremental migration')
if migration['env'].get('RELEASE_CHECKPOINT_VERIFIED') != "${{ steps.checkpoint_receipt.outputs.checkpoint_verified || 'false' }}":
    raise SystemExit('Migration must consume the verified sanitized /data restore receipt.')
download = next(step for step in steps if step.get('name') == 'Download sanitized trusted checkpoint receipt')
if download['with'].get('run-id') != '${{ fromJSON(inputs.config_json).checkpoint_run_id }}':
    raise SystemExit('Baseline/migration must retrieve the receipt from checkpoint_run_id.')
if operation.get('outputs', {}).get('data_run_id') != '${{ github.run_id }}':
    raise SystemExit('The reusable workflow must publish data_run_id as a locator only.')
PY

echo 'UAT Accounts durable checkpoint gate passed.'
