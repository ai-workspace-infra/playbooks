# Shared PROD public ingress

Run `web-saas-public-ingress.yml` against one IaC CMDB host. Supply generation
(`prod-v3`, `prod-v4`, etc.), the GitOps `WebSaasPublicIngress` declaration,
and decoded PEM fields through a protected runtime extra-vars file.
The controller reads only `kv/CICD/domains/svc.plus`; values never enter GitOps,
argv or public evidence. Certificate installation reuses `docker/caddy_certificate_restore`.

The role checks actual hostname, cert/key/expiry and SANs, retains existing
internal sites, stages an idempotent public-site block, validates in the running
immutable Caddy image without networking, and backs up the prior configuration.
It never reads or changes a database, DNS, or another generation, and never
restarts services. Billing retains the recovered generation's probe-only public policy.

For each generation: restore/validate certificate and prepare configuration;
select an immutable GitOps release mounting `/etc/xcontrol/tls` read-only;
let Doco-CD recreate Caddy; probe all public SNI with `curl --resolve` and normal
CA verification before any separately authorized DNS handoff.
