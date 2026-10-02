#!/usr/bin/env bash
set -euo pipefail

: "${MATRIX_HOST:?MATRIX_HOST is required}"
: "${ANSIBLE_INVENTORY:?ANSIBLE_INVENTORY is required}"
: "${VECTOR_BILLING_INGEST_URL:?VECTOR_BILLING_INGEST_URL is required}"

if [[ ! "${VECTOR_BILLING_INGEST_URL}" =~ ^https://[^/]+/v1/ingest/snapshots$ ]]; then
  echo "::error::VECTOR_BILLING_INGEST_URL must be an HTTPS Billing ingest endpoint: ${VECTOR_BILLING_INGEST_URL}" >&2
  exit 1
fi

remote_script=$(cat <<'REMOTE'
set -eu

# The monitor job runs on every host. Only an Agent Proxy owns the Xray
# services; other hosts are intentionally skipped.
if ! systemctl list-unit-files --type=service --no-legend 2>/dev/null | awk '{print $1}' | grep -qx 'xray.service'; then
  echo "xray-billing-chain: SKIP (host does not own xray.service)"
  exit 0
fi

for unit in xray.service xray-tcp.service xray-exporter-xhttp.service xray-exporter-tcp.service vector.service; do
  systemctl is-active --quiet "${unit}" || {
    echo "xray-billing-chain: ${unit} is not active" >&2
    exit 1
  }
done

test -s /etc/vector/vector.toml
grep -Fq 'VECTOR_SNAPSHOT_URL=http://127.0.0.1:8686' /etc/xray-exporter.env
grep -Fq '[sources.xray_snapshot_input]' /etc/vector/vector.toml
grep -Fq '[sinks.billing_snapshot_ingest]' /etc/vector/vector.toml
grep -Fq "__BILLING_INGEST_URL__" /etc/vector/vector.toml

for unit in xray-exporter-xhttp.service xray-exporter-tcp.service; do
  unit_definition="$(systemctl cat "${unit}")"
  printf '%s\n' "${unit_definition}" | grep -Fq -- '--node-id '
  printf '%s\n' "${unit_definition}" | grep -Fq -- '--snapshot-store-path '
done

for endpoint in http://127.0.0.1:8080/scrape http://127.0.0.1:8081/scrape; do
  code=$(curl --silent --show-error --max-time 10 -o /dev/null -w '%{http_code}' "${endpoint}")
  test "${code}" = 200 || {
    echo "xray-billing-chain: ${endpoint} returned HTTP ${code}" >&2
    exit 1
  }
done

# A configured listener does not prove that the exporter supports snapshots.
# Wait for both transport-specific stores so an old metrics-only binary cannot
# pass the deployment while leaving Billing/PostgreSQL permanently empty.
snapshot_deadline=$(( $(date +%s) + 150 ))
while :; do
  if test -s /var/lib/xray-exporter/xhttp-snapshots.json &&
     test -s /var/lib/xray-exporter/tcp-snapshots.json; then
    break
  fi
  if [ "$(date +%s)" -ge "${snapshot_deadline}" ]; then
    echo "xray-billing-chain: exporters did not create both snapshot stores within 150 seconds" >&2
    exit 1
  fi
  sleep 5
done

for snapshot_store in /var/lib/xray-exporter/xhttp-snapshots.json /var/lib/xray-exporter/tcp-snapshots.json; do
  jq -e '
    type == "array" and length > 0 and
    (.[-1].node_id | type == "string" and length > 0) and
    (.[-1].env | type == "string" and length > 0) and
    (.[-1].collected_at | type == "string" and length > 0)
  ' "${snapshot_store}" >/dev/null || {
    echo "xray-billing-chain: invalid or empty snapshot store ${snapshot_store}" >&2
    exit 1
  }
done

 # systemd can report Vector active before its HTTP listener has finished
 # binding (observed on slower regional nodes). Wait briefly for readiness so
 # the verification checks the service contract rather than startup timing.
 vector_deadline=$(( $(date +%s) + 60 ))
 vector_code=000
 while :; do
   vector_code=$(curl --silent --show-error --max-time 10 -o /dev/null -w '%{http_code}' http://127.0.0.1:8686/ || true)
   case "${vector_code}" in
     2??|3??|4??) break ;;
   esac
   if [ "$(date +%s)" -ge "${vector_deadline}" ]; then
     echo "xray-billing-chain: Vector snapshot listener is unavailable (HTTP ${vector_code})" >&2
     exit 1
   fi
   sleep 2
 done

billing_body=$(mktemp)
trap 'rm -f "${billing_body}"' EXIT
billing_auth_header="$(sed -n '/^\[sinks\.billing_snapshot_ingest\.request\]/{n;s/^headers\.Authorization = "\(.*\)"$/\1/p;}' /etc/vector/vector.toml)"
if [ -z "${billing_auth_header}" ]; then
  echo "xray-billing-chain: Billing Authorization header is missing from Vector config" >&2
  exit 1
fi
billing_code=$(curl --silent --show-error --max-time 15 -X POST \
  -H 'Content-Type: application/json' \
  -H "Authorization: ${billing_auth_header}" \
  --data '{}' -o "${billing_body}" -w '%{http_code}' \
  "__BILLING_INGEST_URL__")
billing_size=$(wc -c <"${billing_body}")
case "${billing_code}" in
  2??|3??|400|422)
    if [ "${billing_size}" -eq 0 ]; then
      echo "xray-billing-chain: Billing ingest endpoint returned an empty response (HTTP ${billing_code})" >&2
      exit 1
    fi
    echo "xray-billing-chain: OK (Xray -> exporter snapshots -> Vector -> Billing endpoint HTTP ${billing_code}, ${billing_size} bytes)"
    ;;
  401|403)
    echo "xray-billing-chain: Billing ingest authentication failed (HTTP ${billing_code})" >&2
    exit 1
    ;;
  404|405|5??|000)
    echo "xray-billing-chain: Billing ingest endpoint is unavailable (HTTP ${billing_code})" >&2
    exit 1
    ;;
  *)
    echo "xray-billing-chain: Billing ingest endpoint returned unexpected HTTP ${billing_code}" >&2
    exit 1
    ;;
esac
REMOTE
)

remote_script=${remote_script//__BILLING_INGEST_URL__/${VECTOR_BILLING_INGEST_URL}}

ansible "${MATRIX_HOST}" \
  -i "${ANSIBLE_INVENTORY}" \
  --become \
  -m ansible.builtin.shell \
  -a "${remote_script}" \
  -o
