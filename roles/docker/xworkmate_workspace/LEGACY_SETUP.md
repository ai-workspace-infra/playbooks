# Explicit installer compatibility entry

Owner: Playbooks `xworkmate_workspace`. Caller: explicit management playbook using `include_role` with `tasks_from: legacy_setup`. This task file does not change the normal role entrypoint.

Set `xworkmate_workspace_legacy_setup_enabled: true`, select `xworkmate_workspace_legacy_setup_script`, and pass installer flags as the list `xworkmate_workspace_legacy_setup_arguments`. Runtime credentials/configuration use `xworkmate_workspace_legacy_setup_environment`; never commit credential values. Default is disabled and check mode never executes the installer. Use an explicit management identity and inventory; these installers are not audit operations.

Scripts: setup-xworkmate-workspace.sh, setup-agent-context.sh. Shell flags and service behavior are preserved for compatibility review. Supply `GITOPS_CHECKOUT` or `PLAYBOOKS_CHECKOUT` explicitly when using a local checkout; repository-relative sibling assumptions are removed. No production installation or idempotency claim is made by the file move. Existing service role tasks are preferred for managed deployments.
