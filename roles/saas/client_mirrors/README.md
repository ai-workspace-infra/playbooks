# client_mirrors (`roles/saas/client_mirrors`)

SaaS/API orchestration and acceptance role for the shared full-product-matrix dual distribution mirrors declared in `gitops/resources/svc.plus/prod/client-mirrors/mirrors.yaml`:

- **Layer split**: Cloud IaC state & resource definitions (`cloudflare_r2_bucket`, `alicloud_oss_bucket`, `alicloud_cdn_domain_new`, `alicloud_ram_*`) live under `ai-workspace-infra/iac_modules/terraform-hcl-standard/client-mirrors/`. This SaaS role handles controller-side API preflight, Cloudflare TXT verification for Aliyun CDN, SaaS API reconciliation, Vault KV credential reuse/sync, and end-to-end mirror smoke probes.
- **Global Mirror (`https://dl.svc.plus`)**: Cloudflare R2 bucket (`apac`), custom domain (`dl.svc.plus`, `minTLS >= 1.2`), CORS for `https://console.svc.plus` / `https://xworktech.com`, and a bucket-scoped `Workers R2 Storage Bucket Item Write` Account API token (S3 `access_key_id` + `sha256` `secret_access_key` written to `kv/prod/platform/storage/cloudflare-r2`).
- **CN Mirror (`https://cn-dl.svc.plus`)**: Aliyun OSS (`cn-hongkong`, `public-read`, versioning, AES256, CORS), Aliyun CDN domain (`download`, `scope = overseas` with ICP-filed `svc.plus` ready to switch in `mirrors.yaml`), `limit_rate` per-request throttling, Aliyun CDN `verification.svc.plus` TXT ownership sync in Cloudflare, non-proxied Cloudflare CNAME, and a least-privilege RAM OSS publisher user (`releases/*` & `offline-package/*` written to `kv/prod/platform/storage/aliyun-oss`).
- **Verification**: Writes `releases/_health/probe.json` to both object stores and probes `https://dl.svc.plus` and `https://cn-dl.svc.plus` for HTTP `200` and `Access-Control-Allow-Origin`.

## Inputs (`defaults/main.yml`)

| Variable | Default / Env fallback | Description |
| :--- | :--- | :--- |
| `client_mirrors_manifest_path` | `{{ playbook_dir }}/../gitops/resources/svc.plus/prod/client-mirrors/mirrors.yaml` | Path to GitOps `ClientMirrorStack` YAML |
| `client_mirrors_mode` | `CLIENT_MIRRORS_MODE` (`plan`) | `plan`, `apply`, or `verify` |
| `client_mirrors_apply` | `CLIENT_MIRRORS_APPLY` (`false`) | Mutate cloud APIs and sync Vault KV (auto-true when `mode == apply`) |
| `client_mirrors_rotate_credentials` | `false` | Force issuing a new R2 Account Token and RAM AccessKey even when Vault already stores credentials |
| `client_mirrors_run_cdn_verify_txt` | `true` | Sync `verification.<zone>` TXT and verify Aliyun CDN domain ownership |
| `client_mirrors_vault_addr` | `VAULT_ADDR` (`https://vault.svc.plus`) | Vault HTTP(S) URL |
| `client_mirrors_vault_token` | `VAULT_TOKEN` | Vault token used to read `kv/prod/serverless/cloudflare` & `kv/prod/platform/alicloud` and write `kv/prod/platform/storage/*` |

## Prerequisites (Reuse Existing Cloudflare Secret + One-Time Aliyun Secret)

`kv/prod/serverless/cloudflare` (`CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN`) already exists in `https://vault.svc.plus` and is reused automatically. Only `kv/prod/platform/alicloud` needs one-time bootstrap if not yet written:

```bash
cd /Users/shenlan/workspaces/ai-workspace-infra/playbooks/roles/saas/client_mirrors/files
export VAULT_ADDR="https://vault.svc.plus"
./bootstrap-admin-vault-kv.sh --provider cloudflare --preflight
./bootstrap-admin-vault-kv.sh --provider alicloud
```

## Execution

```bash
cd /Users/shenlan/workspaces/ai-workspace-infra/playbooks

# 1) Read-only plan check
ansible-playbook -i 'localhost,' configure_client_mirrors.yml \
  -e client_mirrors_mode=plan

# 2) Full reconcile + Vault KV sync + dual-mirror smoke verification
ansible-playbook -i 'localhost,' configure_client_mirrors.yml \
  -e client_mirrors_mode=apply

# 3) Smoke verification only using existing Vault KV storage credentials
ansible-playbook -i 'localhost,' configure_client_mirrors.yml \
  -e client_mirrors_mode=verify
```

## Rollback

- Set `enabled: false` on either mirror in `gitops` `web-saas-prod/download-channels-configmap.yaml` to shift Portal traffic within one cache TTL without deleting buckets or objects.
- Re-run with `client_mirrors_rotate_credentials=true` to rotate compromised R2/RAM keys cleanly in Vault.
