# Shared PROD public ingress

Run `web-saas-public-ingress.yml` against one IaC CMDB host. Supply generation
(`prod-v3`, `prod-v4`, etc.), the GitOps `WebSaasPublicIngress` declaration,
and decoded PEM fields through a protected runtime extra-vars file.
The controller reads only `kv/CICD/domains/svc.plus`; values never enter GitOps,
argv or public evidence. Certificate installation reuses `docker/caddy_certificate_restore`.

The role checks actual hostname, cert/key/expiry and SANs, retains existing
internal sites, stages an idempotent public-site block, validates in the running
immutable Caddy image without networking, and backs up the prior configuration.
It never reads or changes a database, DNS, or another generation. Default runs
only prepare configuration. Set `web_saas_public_ingress_activate=true` explicitly
to restart only `web-saas-caddy` after candidate validation and publication.
After a successful explicit restart, the role records the target generation and
host in a root-only activation marker. Recovery `prepare` uses that marker to
preserve an already activated public-site block across idempotent reruns; a
staged but inactive candidate does not create the marker.
The recovery controller disables Caddy's admin API, so activation uses a short
Caddy restart; Accounts, Billing, Console and PostgreSQL are not restarted.

Public ingress has one application routing policy: Console and Accounts proxy
normal business methods; Billing permits its authenticated snapshot POST and
health probes. There is no candidate/recovery read-only ingress flag or backward
compatibility branch. Migration source read-only controls belong to the source
migration owner. Activation still requires explicit authorization; the marker
records generation and host without transferring database or traffic ownership.

Billing permits `POST /v1/ingest/snapshots` in addition to health probes. The
Authorization header reaches Billing unchanged; Billing enforces its configured
Bearer token and rejects missing or invalid tokens. Caddy does not read or store
the shared token. Other methods and management/job endpoints remain denied.
The internal recovery aliases retain their probe-only policy; collectors use
the stable public `billing.svc.plus` endpoint.

The ingestion chain remains Xray Exporter → Vector → Billing. This Role does
not enable Billing exporter pull or direct Agent-to-Billing reporting. Keep
the existing Vector disk buffer and exporter snapshot retention in place.

Acceptance must cover missing/invalid token = 401, GET ingest = 403, management
POST = 403, and real exporter/Vector delivery followed by new checkpoint,
minute-bucket and ledger timestamps. Readiness alone does not prove ingestion.
The activation marker records the explicit restart operation only; it does not
replace those route and end-to-end acceptance checks.

For each generation: restore/validate certificate and prepare configuration;
select an immutable GitOps release mounting `/etc/xcontrol/tls` read-only;
let Doco-CD recreate Caddy; probe all public SNI with `curl --resolve` and normal
CA verification before any separately authorized DNS handoff.
