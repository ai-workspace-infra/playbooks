# AI Memory Hub role

Deploys the AI Memory Hub Compose stack on one explicitly selected inventory host or group. The role does not create credentials. The caller must inject stable secret values from Vault/OIDC into the Ansible controller environment before deployment; missing values cause a fail-closed validation error.

Required controller environment variables:

- `AI_MEMORY_HUB_POSTGRES_PASSWORD` (at least 24 characters)
- `AI_MEMORY_HUB_QMD_INGEST_TOKEN` (at least 32 characters)
- `AI_MEMORY_HUB_QMD_MCP_TOKEN` (at least 32 characters)

Example, with secrets already made available to the process by the approved Vault/OIDC flow:

```bash
ansible-playbook -i inventory.ini deploy_ai_memory_hub.yml \
  -e 'target_hosts=<inventory-host-or-group>'
```

Dynamic CMDB inventory is supported but not required. The `-e` value selects a host or group already present in whichever inventory you pass with `-i`; static inventory and a scoped one-off inventory are also valid. The playbook has no implicit `localhost` target and rejects broad `all` targeting. The Compose process receives the secrets as environment variables, so restrict access to the Docker daemon and its metadata to trusted operators.
