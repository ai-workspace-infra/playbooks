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
name `observability-audit-<scope>`, cluster/environment, externally verified node
set and absolute evidence
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
  -e '{"observability_audit_expected_nodes":["node-a","node-b"]}' \
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
The CLI requires repeatable `--expected-node node-a --expected-node node-b`;
these must come from a separately verified management/provider inventory, not
the KSM response being tested. Missing, invalid or duplicate node inputs fail
before Vault is read. The report records this external-input provenance; it
does not manufacture management evidence for a supplied node list.

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

The token's actual `/api/user` login/name/org must match the separately verified
Viewer service account and `isGrafanaAdmin` must be false. Legacy positive user
IDs must match the service-account ID; the observed Grafana 13 DTO with `id: 0`
is accepted only with exact `uid: service-account:<expected-id>`. A conflicting
service-account UID is rejected even when a numeric ID matches. The verified
management role remains Viewer; null role in the user DTO does not remove this
requirement. Permissions are then read
through `/api/access-control/user/permissions`; write/create/delete/admin
actions block further queries. Missing permission introspection is NOT_RUN,
not proof of isolation. No real production write/DELETE denial probe occurs.

Within the same Viewer token lifecycle, GET requests inspect Grafana-managed
rule configuration via `/api/ruler/grafana/api/v1/rules` and, if allowed,
`/api/v1/provisioning/alert-rules`. Only rule UID, expression hashes and matched
container metric/legacy resource-label names enter evidence. Rule/annotation
text is not persisted. OTel-specific metric/label candidates block pending
review; shared container metrics remain NOT_RUN until actual old/new metric-set
comparison. No permissions or identities are expanded for a forbidden endpoint.

A valid response alone does not establish full rule visibility. Complete
Ruler scope conservatively requires all-folder rule/folder reads and
all-datasource query permissions. Provisioning completeness accepts a dedicated
provisioning read action; for the source-verified Grafana 13.2.4 API contract it
also accepts `alert.rules:read` plus `folders:read`, both with all-folder scopes.
Its handler uses `CanReadAllRules` and returns all organization rules before
per-folder filtering; global datasource query is not required for this
configuration-only path. Relevant granted action/scopes and the runtime
version are retained as evidence. This is versioned API-contract verification,
not binary/source attestation. See fixed upstream source:
[authorization](https://github.com/grafana/grafana/blob/af4a33e3786594ad72f72d97e6744010330c2977/pkg/services/ngalert/api/authorization.go#L365),
[GetAlertRules](https://github.com/grafana/grafana/blob/af4a33e3786594ad72f72d97e6744010330c2977/pkg/services/ngalert/provisioning/alert_rules.go#L320),
[CanReadAllRules](https://github.com/grafana/grafana/blob/af4a33e3786594ad72f72d97e6744010330c2977/pkg/services/ngalert/accesscontrol/rules.go#L85).
Unknown versions without sufficient explicit read actions, partial scopes,
unsupported schemas and forbidden
reads remain NOT_RUN, never zero rules or proof of no dependency. This check is
limited to Grafana-managed rules, not arbitrary external alerting systems.

GET-only queries verify all original 17 Dashboard UIDs plus StarsL, its variable
model, KSM inventory exactly against the externally expected node set, and
node/process/kubelet/cAdvisor `up` coverage against that same external set,
representative metrics and freshness, the absent-cluster negative case,
duplicate/noncanonical working-set, CPU and RSS series, process scrape errors, and
three original K3s overview queries. Signals and thirty-minute continuity bind
KSM to `job="kube-state-metrics"`, Node to `job="node-exporter"`, Process to
`job="process-exporter"`, and cAdvisor to `job="cadvisor"`. cAdvisor CPU/memory
signals select only non-empty, non-`POD` application containers. Old OTel series
with the same metric name cannot stand in for cAdvisor. Every expected node
must have a qualifying range series, coverage and source-sample gap; missing
nodes cannot disappear from a minimum over only returned series. Scrape `up`
freshness is checked as well as its value.

The VM active-target GET endpoint is reduced to counts, health, freshness and
per-node coverage for the exact cluster/environment. URLs, discovered labels
and raw error text never enter evidence. Duplicate same-node targets are
reported even when the backend would merge identical series. Unavailable target-list capability is NOT_RUN, never healthy zero. An
empty or incomplete scoped list is FAIL: the target deployment uses VM
`-promscrape.config` as the canonical scraper for these five jobs. VM target
metadata cannot establish completeness for an unrelated external scraper;
such a deployment needs its own target evidence. `up` and source-series checks
remain independently mandatory.

Container duplicate/noncanonical checks use samples no older than 45 seconds,
non-empty namespace/pod/container and exclude `POD`; they retain CPU's `cpu`
dimension so legitimate per-CPU series do not count as duplicates. They inspect
all jobs to detect cross-collector overlap. Noncanonical-job checks deliberately
retain all label shapes, including old OTel series without raw cAdvisor labels. Network sandbox and host-total
semantics are untouched. Each verified node must report positive process
counts. Fresh process partial/procread errors are reported as NOT_RUN coverage
limitations when nonzero or missing; expected nonroot restrictions are never
relabeled as full-visibility PASS, and no extra host permissions are granted.

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
Additional tests cover the observed service-account DTO, mismatched identities,
forbidden/partial/malformed rule reads, and expression redaction.
Regression fixtures include canonical cAdvisor absent while old OTel data
exists, incomplete per-node range results, and missing/unexpected KSM nodes.

Rollback of this owner is a focused PR revert. Its runtime cleanup is token
revocation, not a service restart, account deletion, database/DNS change or
credential rotation. An existing exact-name non-Viewer account is an explicit
conflict to resolve through its owning authorization process, never an excuse
to widen permissions or reuse administrator credentials for audit.


## Optional full Dashboard query manifest

`--dashboard-query-manifest /absolute/reviewed-manifest.yaml` (Role input
`observability_audit_dashboard_query_manifest`, empty by default) loads the
bounded YAML before Vault or token operations. Generate it with the separate
host Dashboard scope owner, choosing the actual deployed model source. The
owner never silently adapts an old live Dashboard into a newer one: exact
query-variable/target contracts must match. Grafana-only ID/version metadata
is excluded from semantic comparison. New undeployed models explicitly fail
comparison against old live models. A manifest generated from old live models
can inspect that exact query set, preserving its unresolved collection/scope gaps.

Execution reuses the existing independently verified Viewer callback inside the
same token lifecycle; no token parameter, alternate origin, credentials or write
path is added. Identity/permission gates run first; cleanup remains in `finally`.
The executor permits only fixed Dashboard model GETs and fixed datasource query
GETs, at most 500 queries / 300 seconds, backend timeout 5s, existing HTTP timeout
15s / 2 MB response bound, 5000 vector rows and 200 variable choices. Output has
UIDs, target IDs, expression hashes, counts and statuses only, not raw expressions,
labels, process names or samples.

Resolve known variables from real metric label queries in dependency order;
use explicit cluster/environment, selected datasource and fixed bounded time
macros (3m rate / 1m interval / 30m range). Discovered option unions test the
combined scope, not every UI selection combination. Query errors/nonfinite
samples/stale required families/missing expected node families FAIL. Unsupported
macros, conditional absence and empty target results remain NOT_RUN. Old live
queries lacking cluster/environment isolation are executed as-is for diagnosis
but remain NOT_RUN, even when nonempty; deliberately excluded IO/FD families
remain NOT_RUN with their profile limitation, not healthy zero. Node variable
choices are restricted to the separately expected node set. Hidden
targets are explicitly skipped. Family freshness and instant target execution
supplement existing external-node continuity checks; they do not prove all UI
transformations, plugin rendering or global alert-rule visibility. No admin query
fallback or permission expansion is allowed. No live acceptance is performed by
adding this implementation.
