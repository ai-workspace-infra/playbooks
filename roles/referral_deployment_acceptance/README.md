# Deployed referral acceptance preflight

Owner: Playbooks; caller: the existing trusted feature delivery controller,
using a fixed Playbooks source SHA. This is the first, partial deployment-bound
adapter, not a complete referral/Sandbox runner. It neither deploys resources nor
reads Vault/Secrets, runs SQL, creates users, calls Stripe or pays rewards. It
keeps overall `BLOCKED` while business cases are `NOT_RUN`, even if all probes
pass. The Accounts localhost-only real-PG/Sandbox test is unchanged.

Run `referral-deployment-acceptance.yml` on the controller only, with explicit
absolute `referral_acceptance_contract`, `referral_acceptance_observation`, the
independently verified artifact SHA256, and an outside-namespace
`referral_acceptance_output`. This writes only a sanitized local receipt. Exit
2 / the final assertion are expected while the required business adapter is
incomplete; do not configure the caller to reinterpret that as success.

## Input and trust contract

The controller must first verify the observation artifact producer SHA/run,
current cloud resource/cluster identity and stage authorization using its
existing receipt verifier. A file SHA256 provides integrity, not authentication;
this adapter cannot establish producer trust from a copied JSON object. The
existing `v5_readonly_acceptance` report alone is insufficient: it does not
contain the full candidate workload/imageID matrix. The delivery owner must
provide current sanitized observations from its supported observation path,
not fake readiness booleans or a separately maintained inventory.

Contract (`schema: referral-deployment/v1`): environment `dev`, stage `sit|uat`,
feature_id `xconnect-referral`, explicit run_id/cluster/instance_id, namespace,
sit_namespace and uat_namespace (distinct), full 64-hex candidate_fingerprint,
40-hex gitops_sha/owner_sha, nonempty data_baseline, repository→40-hex
source_shas, exact HTTPS `origins.accounts` / `origins.console`, and full digest
`images` plus `expected_runtime_image_ids` maps for **caddy, console, accounts,
billing, content, postgresql**. OCI index digests and platform imageIDs are
separate fields; the release owner must verify their relationship. No mutable
image tags or inferred runtime digest are accepted. UAT additionally requires
repository→actual `merge_shas` matching its built source matrix.

Observation (`schema: referral-deployment-observation/v1`) binds the same
stage, feature, cluster, instance, namespace, fingerprint, gitops_sha and origins;
contains UTC `sampled_at` no more than 10 minutes old, current-generation
`flux_ready` / `observed_generation_current` flags, and `workloads` for all six
components with namespace, ready, image and image_id. All must match before any
network request. Explicit routes must come from the reviewed nonproduction
routing declaration; independent origin ownership remains a controller gate.
Unknown data/identity, stale observation, wrong namespace or changed imageID
blocks before HTTPS.

Probes use system TLS trust, no proxies/redirect-following/authentication:
Accounts `/readyz`, Console `/`, Accounts `/api/ping` image binding, then a
malformed `Stripe-Signature` to the webhook expecting 401 `invalid_signature`.
A 503 `stripe_not_configured` fails the signature probe. Bodies, cookies,
credentials and raw errors are never included in the receipt. Successful probe
does not establish no database change; that remains a separate business case.

## Full runtime acceptance and SIT → UAT

Temporary SIT and persistent UAT each run the complete web-saas chain in separate
namespaces on the same Dev cluster. SIT source is a cross-repository fixed PR
candidate. These particular baseline Accounts/Portal/Router changes already
merged; a later replay cannot claim historical pre-merge SIT. Existing domain,
TLS, trusted cluster observation and authorized Sandbox identity must exist
before execution; missing conditions remain BLOCKED. No production fallback.

The remaining real deployed cases are registration/one-time attribution, MFA
where enabled, campaign eligibility/off behavior, exact 10% calculation with no
payout, qualified annual +30 days without changing the provider billing anchor,
7-day freeze boundary, refund/dispute reversal, real Stripe endpoint delivery,
replay idempotency, and actual business DML-allowed/DDL-denied role tests. Retain
provider test object/event/delivery IDs, before/after facts and bounded read-only
DB reconciliation using the appropriate audit role. Signed local httptest,
source code assertions and root/owner SQL are not substitutes. This PR does not
implement or certify those cases; each stays NOT_RUN rather than inheriting
preflight PASS. Further implementation requires the existing application and
DB owners' supported endpoints/roles and actual Sandbox prerequisites.

UAT watches GitOps main persistently. Main watch alone does not discover new
application digests: the existing trusted build/release owner must update the
reviewed release matrix after each authorized application merge, with all
per-repository merge SHAs and immutable artifacts. No new resource executor or
writer is introduced here. The UAT observation and domain probes must be rerun
against its independent namespace. Any merge-built artifact/config/data change
produces a new candidate fingerprint and invalidates affected SIT evidence;
unchanged content may reuse only still-applicable facts, not a prior namespace's
runtime result. Full acceptance and authorization precede merging lane/business
PRs; no merge or cleanup is performed by this role. Temporary SIT cleanup never
includes persistent UAT.

## Bounded live registration sub-runner

`files/live_registration.py` is a separate external HTTPS BFF runner. It first
requires the current bound observation and health/image preflight, then reads
an active campaign through Console, registers two isolated accounts through
Console `/api/auth/register`, logs each in through `/api/auth/login`, and reads
`/api/auth/referrals` through its independent in-memory cookie session. The
invitee registration includes the owner's real referral code; an exact repeated
registration must return 409 `email_already_exists`. It never follows inviteUrl
(the current Accounts response contains a separate marketing URL).

Run on the controller with the same three arguments as accept.py. Runtime-only
`DEV_REFERRAL_SAMPLES_JSON` is a map with exactly owner/invitee, each containing
name/email/password/code. Names must be `referral-{run_id}-{role}`; mailbox local
parts must equal that name under a dedicated `.onwalk.net` test mailbox domain.
Run IDs are unique lowercase alphanumeric/hyphen 8–48 characters. Passwords are
independent 16+ characters. Codes must be actual six-digit mailbox verification
codes, supplied by the approved test mailbox owner. Do not use bootstrap root
credentials, fake codes, disable MFA, or log this environment value. The runner
does not send mail, read a mailbox, configure SMTP, read Stripe secrets, or call
payment APIs. Sessions, cookies and passwords are memory-only; receipts contain
only check states and hashed sample identifiers. Samples are retained; there is
no destructive cleanup.

Source review: Accounts cmd/accountsvc/main.go starts email verification enabled
but disables it for absent/example/incomplete SMTP configuration. Portal
src/app/api/auth/register/route.ts nevertheless always requires a code. Thus
non-delivering SMTP does **not** establish an executable full BFF registration
path. A real test mailbox/code owner is currently a blocker; no product change
or invented verification code is included here. MFA challenges also block this
sub-runner until the existing supported MFA adapter is supplied; no skip PASS.

The updated contract fixes cluster dev-k3s-v5, exact two namespaces and flat
onwalk origins. namespace_uid/node_uid/cluster_uid must be nonempty UUIDs and
match the fresh trusted observation. Each of six workloads must include
namespace_uid and pod_uid plus ready/image/image_id. UID integrity is checked
before any request. The caller still authenticates the observation producer;
file hash is not provenance. A namespace recreated during the run invalidates
its evidence and requires a new observation and rerun.

Successful registration is a partial check only: unpaid referral attribution
is not exposed by Accounts overview, which lists rewards and paid-invite stats.
A bound independent DB audit must establish the exact sample attribution and
one-time binding. `registration_attribution` stays BLOCKED without that evidence.
The full runner always exits 2 with overall BLOCKED. Sandbox delivery, payments,
10%, +30 days/billing anchor, refund/dispute/replay, seven-day boundary and actual
DB DDL-denial remain NOT_RUN. Offline fake transports only validate contracts;
they are never public deployment proof. No runtime invocation was performed.
