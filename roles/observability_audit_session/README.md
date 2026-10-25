# Bounded Grafana audit session

This controller-side owner separates an explicitly authorized management phase
from an independent Viewer query phase. It does not deploy services, read
Kubernetes Secrets, inspect container environments, change RBAC or reset
Grafana passwords. The caller owns environment approval and immutable source
selection; a successful controller exit cannot replace the rollout receipt.

## Inputs and invocation

All targets and names are explicit. Required non-secret inputs are Grafana
HTTPS origin, `VAULT_ADDR` HTTPS origin, environment-scoped logical KV-v2 path,
password field name, verified management username, dedicated service-account
name `observability-audit-<scope>`, cluster/environment and absolute evidence
output path. Default token lifetime is 3600 seconds (allowed 1800–3600).

The password record needs only the explicitly supplied password field; the
management username is a separately verified non-secret argument. No fallback
password, alternate record, password reset or administrator token scraping is
implemented. A Basic-auth rejection fails and stops.

```sh
# Use the existing approved Vault CLI identity or runtime VAULT_TOKEN.
# Do not put secret values in CLI arguments, inventory, files or terminal logs.
VAULT_ADDR=https://vault.example.test \
ansible-playbook -i localhost, observability_audit_session.yml \
  -e observability_audit_base_url=https://grafana.example.test \
  -e observability_audit_environment=prod \
  -e observability_audit_cluster=cluster-name \
  -e observability_audit_vault_path=kv/prod/services/observability \
  -e observability_audit_password_field=grafana_password \
  -e observability_audit_admin_username=admin \
  -e observability_audit_service_account_name=observability-audit-prod \
  -e observability_audit_output_file=/absolute/controlled/evidence.json
```

This defaults to plan-only and does not read Vault. After reviewing target,
scope and release gate, add `-e observability_audit_apply=true`. Ansible check
mode always remains plan-only. The role requires `vault` in the controller
PATH. `vault kv get -format=json` uses the existing approved CLI identity (or
runtime `VAULT_TOKEN`) and is captured directly into process memory. This role
does not log in, write a Vault token cache, persist retrieved values or pass
secrets as process arguments. TLS bypass is rejected. The logical KV path's
environment must match the selected environment.

The direct owner entrypoint `files/session.py --help` lists equivalent
non-secret parameters. Its only stdout is a status summary; detailed sanitized
evidence is mode 0600. No management password, Basic header or Viewer key is
written into evidence. Unexpected exceptions are represented by fixed error
codes rather than exception/response text. Known in-memory credentials are
also scrubbed from the report as a final safeguard.

## Management phase

1. Search service accounts and accept only an exact dedicated-name match.
2. Create it as enabled `Viewer` if absent. Existing disabled/non-Viewer or
   duplicate exact-name matches fail; the role never changes their roles.
3. Mint one random-name token with an explicit finite lifetime. Read back its
   expiration and remaining lifetime before invoking audit queries.
4. Pass the Viewer key directly to the in-process GET-only verifier. The
   management client never performs telemetry queries with its credentials.
5. In `finally`, revoke only this invocation's token ID and confirm it has
   disappeared. If the POST timed out after creation, look up only the random
   exact token name and revoke that token. Other tokens are untouched.

Service accounts are retained by default, including a newly created one. This
implementation offers no service-account deletion switch. Cleanup failure
sets overall FAIL with the token's bounded expiry and manual-action marker;
it cannot be hidden behind successful queries. Abrupt process termination can
prevent `finally`, so server-enforced token expiry is a separate safety bound.

The [Grafana service-account API](https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/api-legacy/serviceaccount/)
defines these endpoints and finite `secondsToLive`; legacy API routes remain
available on Grafana 13. Only the selected service account and this run's token
are management mutation targets.

## Audit phase and evidence

The token's actual `/api/user` ID/org must match the separately verified Viewer
service account and not be a Grafana administrator. Permissions are then read
through `/api/access-control/user/permissions`; write/create/delete/admin
actions block further queries. Missing permission introspection is NOT_RUN,
not proof of isolation. No real production write/DELETE denial probe occurs.

GET-only queries verify all original 17 Dashboard UIDs plus StarsL, its variable
model, all actual KSM nodes against node/process/kubelet/cAdvisor `up` coverage,
representative metrics and freshness, the absent-cluster negative case,
duplicate/noncanonical container-memory series, process scrape errors, and
three original K3s overview queries. Thirty-minute continuity uses source
timestamps per node rather than a count hidden by query lookback.

PVC metrics remain explicitly NOT_RUN until actual volume-plugin applicability
is established; missing metrics are never reported as healthy zero. New agent
series without 30 minutes of history fail continuity until observed long
enough. Management, audit and cleanup have independent result sections.
Deployment, authentication, permission self-inspection and actual rejected
write operations are distinct evidence levels; this report does not claim a
destructive rejection test was performed.

## Verification and rollback

`python3 -m unittest discover -s tests -p 'test_observability_audit_*.py'`
uses fake HTTP/CLI responses only, covering creation/reuse/collision, finite
TTL, timeout recovery, audit exceptions, token cleanup failure, preservation
of other tokens, credential redaction, permission blocking, node coverage and
source-sample gaps. Run the playbook syntax check before use.

Rollback of this owner is a focused PR revert. Its runtime cleanup is token
revocation, not a service restart, account deletion, database/DNS change or
credential rotation. An existing exact-name non-Viewer account is an explicit
conflict to resolve through its owning authorization process, never an excuse
to widen permissions or reuse administrator credentials for audit.
