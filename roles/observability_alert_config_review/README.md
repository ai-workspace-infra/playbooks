# Authorized management review of Grafana alert configuration

This is a one-time **system-management configuration review**, separate from
Viewer telemetry/data acceptance. Use only with a management identity whose
authority to read all rule configuration in the expected organization has
already been verified and explicitly authorized. A caller-supplied evidence
reference records that external authorization; this owner does not invent
it from HTTP 200 or an account name.

The transport permits exactly `GET /api/user`, `GET /api/org` and
`GET /api/v1/provisioning/alert-rules` on one explicit HTTPS origin. Management
self-introspection must prove the exact explicit login, expected org ID and
`isGrafanaAdmin: true`; then the organization endpoint must independently match
that expected ID before any rules are read. A non-server-admin identity fails
without guessing or privilege changes. Only identity-match booleans enter the
report; the raw profile is not retained.
There are no datasource queries, writes, redirects, SA/token operations, role
changes, secret dumping or password resets. Viewer scope checks and their
NOT_RUN outcomes are untouched.

## Inputs and local invocation

The controller uses the existing approved Vault CLI identity or runtime
`VAULT_TOKEN`. The existing audit owner's `vault_password` helper captures
the selected KV-v2 record directly into process memory. The caller supplies
the explicit password field and verified non-secret management username;
neither credential is printed, passed as a process argument or persisted.
TLS verification is mandatory; Basic-auth failure fails without fallback.

```sh
VAULT_ADDR=https://vault.example.test \
python3 roles/observability_alert_config_review/files/review_config.py \
  --base-url https://grafana.example.test \
  --vault-path kv/prod/services/observability \
  --password-field grafana_password --admin-username admin \
  --expected-org-id 1 --environment prod \
  --management-evidence-ref controlled/management-authorization.json \
  --output /absolute/controlled/management-alert-review.json
```

`observability_alert_config_review.yml` exposes equivalent role variables
prefixed `observability_config_review_`. Inputs default empty/invalid, so an
unscoped invocation fails. Normal execution checks configuration only; it has
no apply or mutation switch. Ansible check mode skips credential/API reads.

## Result boundaries

Only rule UID, expression SHA-256 and matched metric/label candidates enter the
rule summary; original rule/query/annotation text is never stored. Evidence
is mode 0600, and stdout contains only the outcome. OTel-specific container
metric candidates, dynamic container metric selectors, or shared container
metrics with old OTel resource-label/job dependencies block the planned
change. Shared metric names alone require an actual old/new metric comparison
and remain NOT_RUN. Unsupported/malformed rule configuration cannot pass.

Zero rules can prove absence of rule dependencies **for the management-reviewed
expected organization**, because the actual identity is verified as the exact
server-administrator login in that organization and authorization is separately
referenced. It does not prove Viewer access to
all folders, business-data health, external alert-manager compatibility,
all organizations, or successful telemetry migration. Combine this evidence
with the independent Viewer queries and the exact approved rollout receipt.
The authority/scope reference is a prerequisite, not a permission escalation.

See the fixed
[Grafana 13.2.4 organization-scoped rule service](https://github.com/grafana/grafana/blob/af4a33e3786594ad72f72d97e6744010330c2977/pkg/services/ngalert/provisioning/alert_rules.go#L320).
It returns all organization rules for an identity with blanket read authority;
otherwise it filters by allowed rule groups. A partial Viewer identity cannot
substitute for that management prerequisite merely because the endpoint returns
an empty list.

## Verification and recovery

`python3 -m unittest discover -s tests -p test_observability_alert_config_review.py`
uses no network or secrets. It tests exact route/method bounds, organization
mismatch, empty rules, OTel dependencies, shared/unknown expressions, redaction,
and absent management authorization references. Run the playbook syntax-check
before use. No remote operation was performed to prepare these fixtures.

No SA, token or configuration is created, so runtime rollback is unnecessary.
Revert the owner PR to remove its entrypoint. A failed review blocks the change
and requires diagnosis; it never widens Viewer/Vault permissions or reruns a
deployment to manufacture a green report.
