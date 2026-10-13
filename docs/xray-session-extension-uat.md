# UAT session extension upgrade

`deploy_xray_session_extension.yml` runs the `xray_session_extension` Role on an
explicit existing target. Pipeline must select that target from validated IaC
CMDB/GitOps and provide immutable artifact URLs, full source SHA, SHA-256 checksums,
release tag, run ID, and the declared UAT controller origin. UAT is the only accepted
environment; the existing Agent controller URL must match the selected origin.

The Role requires existing Xray/Agent binaries, active-runtime configuration paths,
dynamic API targets and /data. It downloads checksum-bound candidates, checks the
core extension command and both Xray configurations, and checkpoints original
binaries/configuration at `/data/backups/web-saas/uat/<tag>/<run-id>/xray-session-extension/`
with private permissions. Configuration values and credentials are never logged.
It activates the binaries and terminateSessions once, checks service health, and
restores original binaries/configuration if activation fails. It does not restart
Caddy or alter cloud resources, DNS, Accounts users, database schema, or financial
records.

This upgrade is not the quota acceptance itself. After activation, use dedicated
UAT users to prove Accounts exhaustion/SSE → Agent revocation → selected sessions
closed, unaffected user traffic continuing, same Xray/Caddy PIDs, and quota recovery
via hot addition. Keep the P0-A card In Progress until that evidence exists.

Local validation: Ansible syntax check passed. No live upgrade has run. Read-only
access attempts to the published UAT Agent Server inventory on 2026-10-13 encountered
privilege escalation timeout and SSH banner timeout. Resolve target/CMDB and access
through the existing Pipeline authorization path before execution; do not expand a
firewall or write static secrets in this Role.
