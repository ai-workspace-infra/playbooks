# PR: fix/vault-token-safe-default

## Summary
Add defensive `default('')` to `vault_token` resolution in `write_passwords_to_vault.yml`
to prevent undefined variable errors in standalone first-run deployments.

## Context
On fresh standalone deployments where no external Vault exists and no
`VAULT_SERVER_ROOT_ACCESS_TOKEN` / `VAULT_TOKEN` env vars are set, the
`vault_token` variable could evaluate to undefined in edge cases, causing
`write_passwords_to_vault.yml` to fail with:

```
Error while resolving value for 'msg': 'vault_token' is undefined
```

The fix adds an explicit `| default('', true)` to the inner `lookup('env', 'VAULT_TOKEN')`
call, ensuring the variable always resolves to at least an empty string.

## Changes
| File | Change |
|------|--------|
| `write_passwords_to_vault.yml:14` | Add `\| default('', true)` to inner VAULT_TOKEN lookup |

## Related
- xworkspace-console PR: `fix/playbooks-branch-default`
- Conversation: `2f521e13-c13e-4df8-b2d9-cd8883afff30`

## Verification
- Deploy in standalone mode with no VAULT_TOKEN env var set
- `write_passwords_to_vault.yml` should skip KV writes gracefully (empty token → skip)
