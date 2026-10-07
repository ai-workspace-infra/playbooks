#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/native_access_guard.sh"
export AVAILABILITY_RECEIPT_FILE="$RUNNER_TEMP/prod-availability-receipt.json"
test ! -e "$AVAILABILITY_RECEIPT_FILE"
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."
"$RUNNER_TEMP/prod-native-ansible/bin/ansible-playbook" -i "$CMDB_DIR/inventory.ini" \
 --limit web-saas-prod --private-key "$expected_dir/id_ed25519" check-web-saas-availability.yml
# Check HTTPS from the runner against the authoritative target IP, bypassing DNS.
for hostname in accounts.svc.plus billing.svc.plus; do
  status="$(curl --noproxy '*' -sS --max-time 15 --resolve "$hostname:443:$ip" \
    -o /dev/null -w '%{http_code}' "https://$hostname/healthz")"
  [[ "$status" == 200 ]] || exit 1
done
python3 - "$AVAILABILITY_RECEIPT_FILE" <<'CHECK'
import json,sys
r=json.load(open(sys.argv[1]))
assert r['result']=='available' and r['host']=='web-saas-prod' and r['environment']=='prod'
assert all(r[k] is True for k in ('doco_synced','containers_healthy','caddy_running','https_available','api_available','db_available'))
assert r['target_writes'] is False and r['database_cutover_approved'] is False
CHECK
