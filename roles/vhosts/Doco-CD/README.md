# Doco-CD

Deploys [Doco-CD](https://doco.cd/latest/Getting-Started/) with Docker Compose.

## Inputs

- `doco_cd_git_access_token`: **optional** Git access token, only needed for
  private repositories. The default reads `DOCO_CD_GIT_ACCESS_TOKEN`, then
  `kv/CICD` field `DOCO_CD_GIT_ACCESS_TOKEN` from Vault KV v2. When empty,
  `GIT_ACCESS_TOKEN` is omitted from the rendered compose file entirely —
  public repositories need no credential, and requiring one would block the
  deployment on a secret that does not exist.
- `doco_cd_vault_addr`: Vault address, defaulting to `VAULT_ADDR` or
  `https://vault.svc.plus`.
- `doco_cd_vault_jwt_token` / `doco_cd_vault_token`: short-lived Vault
  authentication values supplied by the runtime. JWT is preferred.
- `doco_cd_enable_webhook`: enables `WEBHOOK_SECRET` when true.
- `doco_cd_poll_config`: optional list rendered as Doco-CD `POLL_CONFIG`.
- `doco_cd_image`, `doco_cd_base_dir`, `doco_cd_webhook_port`, and
  `doco_cd_metrics_port`: deployment settings.

## Deploy

```bash
cd playbooks
export VAULT_ADDR=https://vault.svc.plus
export VAULT_JWT_TOKEN=...
ansible-playbook -i inventory.ini setup-Doco-CD.yaml
```

The role fails when the Git access token is unavailable. It does not provide a
placeholder credential or store Vault authentication values in the repository.

## Rollback

Set `doco_cd_image` to the previously validated image tag and rerun the
playbook. Persistent Doco-CD data remains in the Docker `data` volume.
