# Select a release for an existing Doco-CD installation

The caller supplies the verified inventory, exact immutable GitOps tag/SHA and deployment target. This role changes only the existing `POLL_CONFIG`, validates Compose without printing secrets and recreates the controller only when its configuration changed or it is not running. An identical running selection reports no change. It preserves the pinned controller image, credentials, mounts and timezone; no Docker installation or application-container deletion is performed.

Use `select-doco-cd-release.yml` with `doco_release_host`, `doco_release_repository`, `doco_release_reference` and `doco_release_target`. Approval, tag/SHA and artifact evidence verification belong to the caller. Reconciliation success and application/database acceptance must be checked after execution; controller health alone is insufficient.

`doco_release_owner` and `doco_release_group` default to `root`; local fixture callers may explicitly select their current user/group.
