# Selfhost database operation contract

The reusable workflow `.github/workflows/selfhost-database-operations.yml` is the Playbooks execution owner. Its `workflow_call` interface is exactly:

| Input | Required | Meaning |
| --- | --- | --- |
| `environment` | yes | `uat`, `prod`, or `sit`; selects the protected GitHub environment and environment-scoped Vault role. |
| `operation` | yes | Exactly `selfhost_probe`, `selfhost_init`, or `selfhost_verify`. Legacy import is owned by the unified control plane. |
| `release_tag` | no | Immutable deployed release tag; required for verification and matched to the Accounts checkout. |
| `accounts_ref` | no | Accounts source ref; explicit initialization requires an immutable release tag and verifies it resolves to the checkout. |
| `config_json` | no | Nonsecret JSON. Requires `target_host` and `caller_run_id`; probe requires `action` (`baseline` or `probe`) and `acceptance_run_id`; verify also requires `baseline_data_run_id`. |
| `correlation_id` | yes | Plain identifier used for concurrency and evidence correlation. |

The accepted CMDB contract is the current GCP-generated flat JSON object: top-level `environment` matching the requested environment, plus a `web-saas-<environment>` record containing `ip`, `groups` with `web_saas`, and optionally `ansible_user`. The child also confirms the caller run belongs to `ai-workspace-infra/platform-ops-toolkit` and the approved `.github/workflows/selfhost-orchestrator.yml`. AWS/Vultr CMDB shapes without top-level environment identity deliberately fail closed; no environment is inferred from arbitrary DNS/IP data.

SSH identity is checked before connecting: exact canonical host, CMDB environment and group, valid IP, and shell-safe `ansible_user`; non-root users run through `sudo -n`. Initialization is explicit only, requires an immutable Accounts release tag, and stops before Ansible on any nonempty account database. It uses only `kv/data/<environment>/databases` keys `postgres_root_password` and `account_pg_password`; read-only probe/verify fetch only the environment-scoped deploy key.

Baseline receipts contain parent/child run IDs, host/correlation IDs, captured database state, migration state, and row counts. Row fingerprints remain on the target host. Verification downloads the receipt using its actual child run ID and checks parent/child/host bindings. `legacy_import` remains outside this executor and requires explicit confirmation through the unified operation workflow.
