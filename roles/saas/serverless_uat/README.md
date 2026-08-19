# Ansible Role: `saas/serverless_uat`

该角色用于一键编排和部署基于 Serverless 的 UAT 极简零成本测试栈：
* **GCP Cloud Run**：部署 `accounts`, `billing-service`, `content-service`（默认 `min-instances=0`，闲置零计费）；
* **Cloudflare DNS**：创建 `billing-origin-serverless-uat.onwalk.net` DNS-only CNAME，作为 Billing Origin Rule 的同区回源别名；
* **Cloudflare Worker**：部署 `edge-gateway.svc.plus` 边缘智能网关；
* **Cloudflare Pages**：部署 `portal` 前端控制台；
* **Supabase Cloud Free**：持久化保存测试数据。

## 使用示例

### 1. 部署 UAT 环境
```yaml
- name: Deploy UAT Serverless Stack
  hosts: localhost
  connection: local
  roles:
    - role: saas/serverless_uat
```

Cloudflare Origin Rules 使用 DNS-only 回源别名的 `origin.host`，而
Cloud Run 的 `run.app` 主机名继续作为 Host header 和 TLS SNI。执行主机需要
已认证的 `gcloud`、Cloudflare DNS token（`CLOUDFLARE_API_TOKEN` 或
`CLOUDFLARE_DNS_API_TOKEN`），以及对应 Zone Read + DNS Write 权限；不需要
Cloud Run Preview domain mapping。

### 2. 夜间销毁临时计算
```yaml
- name: Teardown UAT Serverless Stack
  hosts: localhost
  connection: local
  tasks:
    - ansible.builtin.import_role:
        name: saas/serverless_uat
        tasks_from: destroy.yml
```
