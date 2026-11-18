# K3s Platform Migration Role (`k3s_platform_migration`)

Reusable Ansible role and CLI tooling for single-node K3s cross-cloud host-swap verification, second-level incremental catch-up sync, DNS/HTTPS/Vault cutover probing, and source cluster isolation.

## Design Rules
- **Zero hardcoding**: source/target hosts, `ansible_user`, WireGuard tunnel IPs, DNS aliases/domains, and the 42 business tables are loaded dynamically from GitOps manifests (`resources/...` and `operations/accounts-migration/target-business.yaml`).
- **Vault KV credentials**: SSH private keys are fetched dynamically from the GitOps-declared `k3s_platform_vault_ssh_key_path` into an ephemeral `0600` file and deleted on exit.
- **Safe defaults**: `isolate_source` runs in read-only dry-run mode unless `k3s_platform_migration_confirm_source_scale_down=true` (or `--confirm` on CLI).

## Phases (`k3s_platform_migration_phase`)
- `audit` (`files/audit_business_tables.py`): compares declared business table row counts between source and target PostgreSQL pods (supports `--target-only` after source DB scale-down).
- `catchup` (`files/catchup_sync.py`): dumps source DB, transfers over dynamically discovered WireGuard IP (or controller relay), resets target schemas cleanly before `pg_restore --no-owner`, and runs the 42-table audit.
- `probe` (`files/probe_cutover.py`): verifies public DNS resolution, HTTPS endpoints (`--resolve` in `pre-cutover` mode or public DNS in `live` mode), PostgreSQL TCP `:5432`, and `vault status`.
- `isolate_source` (`files/isolate_source_cluster.py`): suspends Flux Kustomizations/HelmReleases on source, scales workload namespaces to 0, and re-runs live cutover probes against target.

## Usage

### Via Ansible Playbook (`k3s_platform_migration_ops.yml`)
```bash
# 1. Audit 42 business tables (live cross-cluster or target-only after cutover)
ansible-playbook -i localhost, k3s_platform_migration_ops.yml -e k3s_platform_migration_phase=audit
ansible-playbook -i localhost, k3s_platform_migration_ops.yml -e k3s_platform_migration_phase=audit -e k3s_platform_migration_audit_target_only=true

# 2. Second-level incremental PostgreSQL catch-up sync
ansible-playbook -i localhost, k3s_platform_migration_ops.yml -e k3s_platform_migration_phase=catchup

# 3. Cutover DNS/HTTPS/TCP/Vault probes
ansible-playbook -i localhost, k3s_platform_migration_ops.yml -e k3s_platform_migration_phase=probe

# 4. Source cluster Flux suspend + scale-to-zero + live target verification
ansible-playbook -i localhost, k3s_platform_migration_ops.yml -e k3s_platform_migration_phase=isolate_source -e k3s_platform_migration_confirm_source_scale_down=true
```

### Direct CLI Execution
```bash
python3 roles/vhosts/k3s_platform_migration/files/audit_business_tables.py --target-only
python3 roles/vhosts/k3s_platform_migration/files/probe_cutover.py --mode live
```
