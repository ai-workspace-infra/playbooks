# PR: fix/vault-auto-unseal

## Summary
Add automatic initialization, unsealing, and root token aliasing for standalone Vault on Linux.

## Context
When running Vault on Linux in standalone mode, it uses the production `-config` option pointing to the PostgreSQL storage backend. Unlike development mode (`-dev`) used on macOS, production mode starts in a **sealed** state.

Furthermore, on persistent environments (like CI/CD runners or long-lived staging VMs), Vault is restarted across runs. A restarted Vault is sealed, causing downstream tasks like KV store check and configuration to fail:
```
Error listing secrets engines: Error making API request.
URL: GET http://127.0.0.1:8200/v1/sys/mounts
Code: 503. Errors:
* Vault is sealed
```

The fix introduces an automated lifecycle manager for standalone Vault:
1. **Status check**: Query if Vault is initialized.
2. **Database Auto-Recovery**: If Vault is initialized but `/etc/vault.d/vault_init.json` is missing (i.e. unseal keys were lost in a previous run), truncate the DB tables (`vault_kv_store`, `vault_ha_locks`) to force Vault to become uninitialized.
3. **Auto-Initialization**: If uninitialized, run `vault operator init` with 1 key share and save the results locally to `/etc/vault.d/vault_init.json` (root-only readable).
4. **Auto-Unseal**: If sealed, read the unseal key and unseal Vault automatically.
5. **Token Registration**: Create a root token matching the unified auth token (`vault_server_root_access_token`) so that other tools can authenticate seamlessly.

## Changes
| File | Change |
|------|--------|
| `roles/vhosts/vault/tasks/main.yml` | Add state verification, auto-init, auto-reset, auto-unseal, and token aliasing blocks. |

## Related
- Console repo PR: #28
- Playbooks PR: #116, #117
- Conversation: `2f521e13-c13e-4df8-b2d9-cd8883afff30`

## Verification
- CI/CD workflow `deploy-ai-workspace-iac.yaml` should deploy successfully.
- Subsequent redeployments should succeed without failing due to sealed Vault.
