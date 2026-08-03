# 2026-08-03 Agent Proxy Vault write interpreter

## Failure

The UAT Agent Proxy Gate reached `Securely write XRAY_UUID to Vault KV if
generated` and failed because `community.hashi_vault.vault_write` could not
import `hvac` from the automatically selected `/usr/bin/python3`.

## Correction

The task is delegated to `localhost`, so it now explicitly sets
`ansible_python_interpreter` to `ansible_playbook_python`. This keeps the
Vault write on the runner and uses the same interpreter installed by the
workflow bootstrap, without changing the remote UAT host or production.

## Verification

The next UAT rerun must pass the Vault UUID write and continue to native
agent-proxy deployment. Keep the same UAT-only parameters: r6, 2C4G,
`web-saas + agent-proxy`, and parameterized UAT DNS update enabled.
