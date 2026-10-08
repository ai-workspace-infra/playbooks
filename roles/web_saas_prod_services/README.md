# PROD Accounts/Billing convergence

`converge-web-saas-prod-services.yml` consumes the IaC CMDB (`CMDB_FILE`),
GitOps `topology/prod/runtime-selection.yaml` (`GITOPS_CHECKOUT`), and an
immutable GitOps ref (`GITOPS_REF`). Use the existing reviewed SSH identity
and known_hosts with `ANSIBLE_HOST_KEY_CHECKING=True` and explicit
`ANSIBLE_SSH_ARGS` overriding the legacy repository defaults.

Vault authentication is runtime-only (`VAULT_TOKEN`); never commit credentials.
The role restores service/TLS material, grants a missing application role access
to existing business tables, installs Doco-CD's `prod-services` target, and
activates Accounts and restricted Billing through the existing system Caddy.

The target excludes PostgreSQL and Console. Existing database container ID,
start time and mounts must stay unchanged. No initialization, schema migration,
snapshot import, user reset, public database port, or formal traffic cutover is
performed. `runtime_role: primary` means serving the existing LOCAL account
database, not promotion of that database to global authority.

The formal traffic declaration remains serverless. Selfhost background writers
remain disabled until the data-authority gate is independently verified;
`jobs.active_runtime: selfhost` is desired ownership, not proof of activation.
Both topologies can remain enabled without switching production DNS/Worker
bindings. Switching requires separate authority and full-chain acceptance.

Acceptance: local `/readyz` on 8080/8081, public Accounts TLS/readiness, Billing
denial from untrusted clients, unchanged database identity/schema/data counts,
and Doco-CD receipt matching the pinned GitOps ref. Health/readiness alone does
not prove authenticated Console login or authoritative billing jobs.
