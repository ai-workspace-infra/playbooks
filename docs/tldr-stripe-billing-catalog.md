# TL;DR: Stripe 套餐目录初始化

`initialize-web-saas-schemas.yml` 现在会在**新建自托管 Accounts 数据库**中检查
`PRO-MONTHLY`：不存在时，从与 `ACCOUNTS_SCHEMA_REF` 相同的 Accounts 版本下载并执行
`scripts/seed-billing-plans.sql`。已存在则跳过，因此日常部署不会覆盖运营人员的目录改动。

本仓只负责数据库套餐种子，**不会**在 Ansible 主机上保存或使用 Stripe 密钥。Stripe
Product、Price、Webhook 和 `billing_plans.stripe_price_id` / 价格快照由
`ai-workspace-infra/platform-ops-toolkit` 在 Accounts 公网端点和 DNS 就绪后同步。

所需密钥只存在于 Vault：

```text
kv/<env>/billing-service: STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET
kv/CICD: ROOT_BOOTSTRAP_PASSWORD（可选 ROOT_BOOTSTRAP_EMAIL）
```

紧急人工同步时，使用 Accounts 仓库的命令，不要把 Markdown URL 放进环境变量值：

```bash
STRIPE_SECRET_KEY='从 Vault 读取' \
ACCOUNTS_ADMIN_TOKEN='短期管理员 token' \
ACCOUNTS_BASE_URL='https://accounts-cloudflare-uat.onwalk.net' \
scripts/stripe-sync-catalog.sh \
  --env uat \
  --domain-base onwalk.net \
  --write-catalog
```

详细发布流程见 platform-ops-toolkit 的
[`Stripe 套餐目录初始化 TL;DR`](https://github.com/ai-workspace-infra/platform-ops-toolkit/blob/main/docs/howto/stripe-billing-catalog-tldr.md)。
