# tky production Xray usage -> UAT Console collection bridge

## Scope

This runbook changes only the usage collection path for
`admin@tky-proxy.svc.plus`:

```text
Xray API 28080/28081
  -> xray-exporter (Prometheus + normalized snapshots)
  -> Vector HTTP source 127.0.0.1:8686
  -> billing-serverless-uat.onwalk.net/v1/ingest/snapshots
  -> UAT Billing / Accounts
  -> console-cloudflare-uat.onwalk.net/panel/ops
```

The existing Xray services, agent service, Vector package, Observability
remote-write destination and every other application binary are left intact.
The exporter is upgraded only to the release that contains the normalized
snapshot push contract: `uat-daily-build-2026.08.21-r8`.

The source remains tagged `env=prod`; the destination is UAT by design. The
Accounts endpoint is the authenticated serverless UAT origin behind the
Cloudflare Console, not the browser-facing Console URL.

## Deployment

Obtain the shared UAT `INTERNAL_SERVICE_TOKEN` from Vault without committing it
or placing it in inventory files, then run from the playbooks repository:

```bash
export INTERNAL_SERVICE_TOKEN='…Vault value…'
ansible-playbook \
  -i inventory/terraform_cmdb.py \
  deploy_tky_prod_uat_billing_fanout.yml
unset INTERNAL_SERVICE_TOKEN
```

The playbook refuses to run without the token. It renders the token only into
the root-owned exporter environment file and the root/vector-readable Vector
configuration (both mode `0600`).

## Verification

On tky:

```bash
systemctl is-active xray.service xray-tcp.service \
  xray-exporter-xhttp.service xray-exporter-tcp.service vector.service
ss -ltn '( sport = :28080 or sport = :28081 or sport = :8080 or sport = :8081 or sport = :8686 )'
curl -fsS http://127.0.0.1:8080/scrape | grep '^xray_up '
curl -fsS http://127.0.0.1:8081/scrape | grep '^xray_up '
journalctl -u xray-exporter-xhttp.service -n 50 --no-pager
journalctl -u vector.service -n 50 --no-pager
```

The exporter logs should show successful snapshot collection/push without
`Billing snapshots disabled`, and Vector should not log repeated sink 401/5xx
responses. Confirm the Billing status endpoint returns a successful
`ingest-snapshot` result after the first one-minute interval, then verify the
UAT Console Ops view with an authenticated operator session.

## Rollback

Re-run the standard exporter/vector deployment with Billing fan-out disabled
and the previous exporter release. This removes the local snapshot source and
stops UAT Billing writes while preserving the existing Prometheus path:

```bash
export XRAY_EXPORTER_VERSION='daily-build-2026.08.11'
export XRAY_EXPORTER_SNAPSHOT_FEATURES_ENABLED='false'
export VECTOR_BILLING_INGEST_ENABLED='false'
ansible-playbook -i inventory/terraform_cmdb.py deploy_xray_exporter.yml
ansible-playbook -i inventory/terraform_cmdb.py \
  -e observability_agent_hosts=tky-proxy.svc.plus \
  deploy_observability_agent.yml
```

Do not delete the exporter snapshot store during rollback; it is local,
replay-safe state and may be needed for an audit.
