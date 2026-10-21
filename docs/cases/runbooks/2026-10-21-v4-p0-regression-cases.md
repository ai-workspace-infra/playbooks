# V4 upgrade regression cases — 2026-10-21

Historical incident index: [Knowledge V2/V3 upgrade and V3/V4 incident record](https://github.com/ai-workspace-services/knowledge/blob/main/content/02-iac-devops/cloud-infrastructure-devsecops-baseline/15-prod-v2-v3-snapshots-and-upgrade-retrospective.zh.md), with the [V3→V4 P0 event timeline and case mapping](https://github.com/ai-workspace-services/knowledge/blob/main/content/02-iac-devops/cloud-infrastructure-devsecops-baseline/records/prod-v2-v3-20261019/20261021-prod-v3-v4-p0-regression-record.zh.md).

These cases are per-incident acceptance gates. Run against an isolated candidate or Sandbox unless a case explicitly calls for read-only production observation. Never synthesize production payment or traffic facts. A green process/readiness probe alone does not pass the related business case.

| Case ID | Regression test / evidence |
| --- | --- |
| V4-P0-DB-ROLE | `tests/data_operations/selfhost/v4_business_role_acceptance.sh` (read-only on candidate) plus disposable PostgreSQL 17 grant test; verify canonical runtime `account_user` has required business grants. |
| V4-P0-STRIPE-EGRESS | `tests/data_operations/selfhost/v4_stripe_sandbox_egress.sh`; Sandbox-only Accounts-container HTTPS probe. Any valid HTTP response proves DNS/TLS/connectivity; timeout/refusal fails. |
| V4-P0-WEBHOOK-SECRET | Accounts `api/billing_p1_test.go` signed test webhook cases plus Stripe Sandbox endpoint check; valid signature is accepted, wrong secret rejected, duplicate event is idempotent. Never replay into production. |
| V4-P0-AGENT-INGRESS | Isolated route integration test: authenticated agent status POST succeeds and updates registration; missing/invalid credential is rejected. This live/API integration case still needs its fixture harness; do not infer POST support from GET health checks. |
| V4-P0-EXPORTER-TOKEN | `tests/test_snapshot_credential_repair.py`; runtime authenticated `GET /v1/snapshots/latest` returns fresh JSON after restart, and the same-version rerun causes no restart. |
| V4-P0-TRAFFIC-CHAIN | `tests/data_operations/selfhost/v4_traffic_chain_acceptance.sh`; with an already-connected client, positive-byte minute buckets must arrive in at least two recent windows. Pair with Exporter checkpoints to verify genuine counter growth through Exporter → Vector → Billing. No fake POST or manual ledger write. |
| V4-P0-DUPLICATE-NODE | `tests/data_operations/selfhost/v4_duplicate_producer_identity_test.py`; inventory of all active producers must map each stable node/transport ID to exactly one source. A collision blocks byte-delta acceptance. |
| V4-P0-UI-FRESHNESS | Authenticated browser acceptance: the same account's newly stored minute buckets are included in the last-hour display after refresh; time boundaries use UTC instants consistently. Portal unit coverage is not yet present and remains a follow-up. |
| V4-P0-LOGIN-SESSION | Candidate login integration: valid credentials create a session and return the expected cookie; backend session-store failures are surfaced without a false success. The historical GET `method_not_allowed` response is not a POST regression; test POST only. |
| V4-P0-ACCOUNT-BOOTSTRAP | Authenticated onboarding browser/API test: policy endpoint is available and VLESS node data is present before URI construction; missing node data renders a recoverable empty state rather than `node is undefined`. |

## Candidate result template

Record one row per case in the release receipt:

```yaml
case_id: V4-P0-TRAFFIC-CHAIN
source_sha: <immutable source commit>
image_digest: <deployed digest>
run_id: <unique run id>
status: PASS | FAIL | BLOCKED | NOT_RUN
exit_code: <integer or null>
evidence: <redacted receipt path>
```

Missing prerequisite means `BLOCKED`; not run means `NOT_RUN`. Neither passes the release gate. See `tests/data_operations/selfhost/v4_p0_regression_cases_test.py` for the catalog contract.
