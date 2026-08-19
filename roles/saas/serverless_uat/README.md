# Ansible Role: `saas/serverless_uat`

该角色用于一键编排和部署基于 Serverless 的 UAT 极简零成本测试栈：
* **GCP Cloud Run**：部署 `accounts`, `billing-service`, `content-service`（默认 `min-instances=0`，闲置零计费）；
* **Cloudflare Worker**：部署 Edge Gateway，并将 `billing-serverless-uat.onwalk.net` 自定义域名绑定到 core Worker；
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

core Edge Gateway Worker 代理 Billing 请求到 Cloud Run，因此不依赖 Enterprise
套餐的 Origin Rule Host/SNI override。执行主机需要已认证的 `gcloud`，以及
具备 Zone Read、DNS Write 和 Workers Scripts Write 的 Cloudflare token；不需要
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
