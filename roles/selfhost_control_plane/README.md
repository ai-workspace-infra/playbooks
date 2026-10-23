# Selfhost candidate installation owner

Caller: `scripts/control_plane/entrypoint.py` -> `selfhost-control-plane.yml`.
Inputs: validated `control_plane_contract` and runtime-only
`control_plane_runtime`; see `docs/selfhost-control-plane-contract.md`.
Target: exactly one host from `inventory/terraform_cmdb.py`. Dependencies:
native Docker, host config, Doco-CD, native migratectl and business-permission
owners. Their implementation is reused; domain SQL is not copied here.

First install claims only empty declared storage, installs Docker, restores
provided TLS/configuration, selects PostgreSQL-only Doco-CD at a fixed commit,
initializes native SQL only with explicit eligibility, grants the native business
identity and selects all five applications. No database is dropped or reset.
Root and dedicated storage profiles are supported. The non-secret target journal
binds resource, images, schema and database identity. Identical reruns verify
schema/checkpoint and application password without reinitializing or rotating it.
Changed artifacts require a separately reviewed upgrade, not journal deletion.

Before native initialization, the shared read-only PostgreSQL probe requires
`/proc/1/comm` to be `postgres`, TCP readiness and a successful `SELECT 1`.
The official image's temporary initialization server cannot satisfy this gate.

Check via the runner `--check`; direct Ansible check mode is refused because
native owners have execution-only tasks. No runtime acceptance was performed.
On failure preserve the target and inspect its journal. Do not remove storage or
reapply schema after an ambiguous committed initialization. Rollback before
traffic handoff leaves the old source serving; database restore and cutover need
their own authorization and backup/restore procedure.
