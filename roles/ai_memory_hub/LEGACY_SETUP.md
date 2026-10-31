# Explicit installer compatibility entry

Owner: Playbooks `ai_memory_hub`. Caller: explicit management playbook using `include_role` with `tasks_from: legacy_setup`. This task file does not change the normal role entrypoint.

Set `ai_memory_hub_legacy_setup_enabled: true`, select `ai_memory_hub_legacy_setup_script`, and pass installer flags as the list `ai_memory_hub_legacy_setup_arguments`. Runtime credentials/configuration use `ai_memory_hub_legacy_setup_environment`; never commit credential values. Default is disabled and check mode never executes the installer. Use an explicit management identity and inventory; these installers are not audit operations.

Scripts: setup-ai-memory-hub.sh. Shell flags and service behavior are preserved for compatibility review. Supply `GITOPS_CHECKOUT` or `PLAYBOOKS_CHECKOUT` explicitly when using a local checkout; repository-relative sibling assumptions are removed. No production installation or idempotency claim is made by the file move. Existing service role tasks are preferred for managed deployments.
