#!/usr/bin/env bash
# Owner: Playbooks. Caller: Toolkit. No cloud mutation or CMDB fabrication.
set -euo pipefail
: "${RUNNER_TEMP:?private runner directory required}"
: "${FULL_BUSINESS_SPEC_FILE:?fixed non-secret source/image contract required}"
: "${FULL_BUSINESS_MODE:?explicit preview/copy/compare/core_users required}"
: "${NATIVE_RECEIPT_FILE:?same-run receipt required}"
[[ "$FULL_BUSINESS_MODE" == preview || "$FULL_BUSINESS_MODE" == copy || "$FULL_BUSINESS_MODE" == compare || "$FULL_BUSINESS_MODE" == core_users ]]
[[ "${NATIVE_DATA_GATE_VERIFIED:-}" == true ]]
[[ "$NATIVE_RECEIPT_FILE" == "$RUNNER_TEMP/prod-full-business-receipt.json" && ! -e "$NATIVE_RECEIPT_FILE" ]] || exit 2
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$script_dir/full_business_host.py" --validate-only --spec "$FULL_BUSINESS_SPEC_FILE" \
  --guard-directory "$script_dir" --mode "$FULL_BUSINESS_MODE"
source "$script_dir/native_access_guard.sh"
# Module arguments and private credential stdin travel over encrypted SSH.
# Disable remote module temp files as well as multiplexed persistent sessions.
export ANSIBLE_PIPELINING=true
owner_root="$(cd "$script_dir/../../.." && pwd)"
cd "$owner_root"
"$RUNNER_TEMP/prod-native-ansible/bin/ansible-playbook" -i "$CMDB_DIR/inventory.ini" \
  --limit web-saas-prod --private-key "$expected_dir/id_ed25519" transfer-web-saas-full-business.yml
test -s "$NATIVE_RECEIPT_FILE"
# Reuse the owner's exact validator; never accept Ansible's empty-host exit 0.
python3 - "$NATIVE_RECEIPT_FILE" "$FULL_BUSINESS_SPEC_FILE" "$FULL_BUSINESS_MODE" "$script_dir" <<'PY'
import json,pathlib,sys
sys.path.insert(0,sys.argv[4])
import full_business_host as host
receipt=json.loads(pathlib.Path(sys.argv[1]).read_text())
spec=json.loads(pathlib.Path(sys.argv[2]).read_text())
host.validate_spec(spec)
host.validate_receipt(receipt,spec,sys.argv[3],receipt['source_identity_sha256'])
assert receipt['host']=='web-saas-prod' and receipt['database']=='account'
assert receipt['accounts_commit']==spec['transfer']['accounts_commit']
assert receipt['image_digest']==spec['transfer']['image_digest']
assert receipt['business_tables']==spec['transfer']['business_tables']
assert receipt['writers_paused'] is True and receipt['independent_disk_verified'] is True
assert receipt['source_writers_paused'] is False and receipt['final_catchup_complete'] is False
PY
