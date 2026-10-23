# XWorkmate Workspace role

Deploys the XWorkmate Workspace Compose stack on one explicitly selected inventory host or group. The role does not create credentials. The caller must inject stable secret values from Vault/OIDC into the Ansible controller environment before deployment; missing values cause a fail-closed validation error.

Required controller environment variables:

- `XWORKMATE_WORKSPACE_AUTH_TOKEN` (at least 32 characters)
- `XWORKMATE_WORKSPACE_POSTGRES_PASSWORD` (at least 24 characters)
- `XWORKMATE_WORKSPACE_QMD_INGEST_TOKEN` (at least 32 characters)
- `XWORKMATE_WORKSPACE_QMD_MCP_TOKEN` (at least 32 characters)

Example, with secrets already made available to the process by the approved Vault/OIDC flow:

```bash
ansible-playbook -i inventory.ini deploy_xworkmate_workspace.yml \
  -e 'target_hosts=<inventory-host-or-group>'
```

Dynamic CMDB inventory is supported but not required. The `-e` value selects a host or group already present in whichever inventory you pass with `-i`; static inventory and a scoped one-off inventory are also valid. The playbook has no implicit `localhost` target and rejects broad `all` targeting. The Compose process receives the secrets as environment variables, so restrict access to the Docker daemon and its metadata to trusted operators. Successful verification prints only whether the authentication token is configured, never its value.
