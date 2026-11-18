# Grafana configuration bundle

This controller-side role reads configuration APIs only. It does not migrate metrics, logs, traces, users, sessions, alert rules, or notification channels. The source transport implements GET only; TLS validation is mandatory. Resolve authentication from the approved Vault record before invocation and pass it as protected variables, never committed inventory values.

Use explicit `prod`/`uat`, source and target HTTPS URLs, GitOps root/output-relative path, and target endpoint overrides for every datasource. Start with `grafana_config_apply: false` or Ansible check mode. Review the generated plan before setting apply true. Apply writes only deterministic Kubernetes YAML inside the selected GitOps workspace; submit it through the repository PR/release process. Flux and the Chart provision the target configuration.

The renderer preserves dashboard/folder/datasource UIDs, panel definitions and variable definitions. Query-variable runtime selections/options are reset so they refresh against the target's new stores. It rewrites only source Grafana navigation to the explicit target URL. Secure datasource/app fields require target environment references. Existing target-only dashboard UIDs must be listed in `reserved_uids`. ConfigMaps are bounded to 700 kB each; empty folders receive a file provider with a non-dashboard marker.

After deployment use operation `verify` with the generated inventory. It checks UIDs, folder placement, panel/variable/navigation hashes and datasource identities against actual APIs. Re-run the same export three times: unchanged files and identical bundle checksum prove rendering idempotence. This is distinct from runtime reconciliation and business acceptance.

Unsigned/custom plugins require a reviewed immutable archive plus SHA-256 in the Chart declaration. App settings with identity credentials must not be enabled before the target resolver accepts its dedicated credential. Credentials are neither copied from API responses nor written into generated YAML.
