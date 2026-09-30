# Zitadel Docker Role (IAM)

This role deploys the Zitadel stack (Zitadel core + Next.js login UI frontend) onto the target host and configures reverse proxies in Caddy.

> [!IMPORTANT]
> **Database & Architecture**: This version connects directly to the shared Postgres server (`postgresql-svc-plus` over the `docker_postgres_network`), rather than using a local standalone database container. It also sets memory resource limits (`300M` for Zitadel and `200M` for the login UI) to prevent system memory exhaustion (OOM).

---

## TLDR - 快速使用与配置同步

### 1. 配置准备

在执行部署前，需要配置 Vault 环境变量，该角色会自动从 Vault 中拉取 `iam.svc.plus` 路径下的 `zitadel-admin` 初始密码，且通过 `no_log: true` 保证密钥在整个执行日志中**完全不落盘、不泄露**。

```bash
# 环境变量配置
export VAULT_ADDR=https://vault.svc.plus
export VAULT_TOKEN=hvs.YOUR_VAULT_TOKEN_HERE  # 或使用 VAULT_SERVER_ROOT_ACCESS_TOKEN
```

同时支持在执行命令中以 `-e` 额外参数的形式传入 Vault 地址及令牌：

```bash
ansible-playbook -i inventory.ini deploy_zitadel_docker.yaml \
  -l jp-xhttp-contabo.svc.plus \
  -e "domain=iam.svc.plus" \
  -e "vault_addr=https://vault.svc.plus" \
  -e "vault_token=hvs.YOUR_VAULT_TOKEN_HERE" \
  -C -D
```

---

### 2. 追齐命令 (Sync & Deploy)

为了安全同步线上和本地的配置，请分两步执行：

#### 步骤 A：Dry Run 检查与差异比对 (Check Mode)
运行以下命令进行配置对比，此模式不会对远程服务器进行实际修改：
```bash
export VAULT_ADDR=https://vault.svc.plus
export VAULT_SERVER_ROOT_ACCESS_TOKEN=hvs.YOUR_VAULT_TOKEN_HERE

ansible-playbook -i inventory.ini deploy_zitadel_docker.yaml \
  -l jp-xhttp-contabo.svc.plus \
  -e "domain=iam.svc.plus" \
  -C -D
```

#### 步骤 B：执行真实部署与同步 (Apply Mode)
确认 Diff 差异符合预期后，移去 `-C` 选项，运行以下命令真实地将本地模板和设置推送到线上：
```bash
export VAULT_ADDR=https://vault.svc.plus
export VAULT_SERVER_ROOT_ACCESS_TOKEN=hvs.YOUR_VAULT_TOKEN_HERE

ansible-playbook -i inventory.ini deploy_zitadel_docker.yaml \
  -l jp-xhttp-contabo.svc.plus \
  -e "domain=iam.svc.plus" \
  -D
```

---

## Defaults

*   `zitadel_deploy_dir`: `/opt/zitadel`
*   `zitadel_domain`: `iam.svc.plus`
*   `zitadel_masterkey`: `MasterkeyNeedsToHave32Characters`
*   `zitadel_api_bind_host`: `127.0.0.1`
*   `zitadel_api_port`: `19080`
*   `zitadel_login_bind_host`: `127.0.0.1`
*   `zitadel_login_port`: `19081`
*   `zitadel_caddy_conf_dir`: `/etc/caddy/conf.d`
*   `zitadel_caddy_fragment_path`: `/etc/caddy/conf.d/{{ zitadel_domain }}.caddy`

## ZITADEL 单机 GitOps（新入口）

`deploy_zitadel_docker.yaml` 默认 `zitadel_deployment_mode=doco-cd`。
`deploy_iam_domain.yml` 先准备单机 PostgreSQL 和 IAM 数据库，再调用该入口。
Ansible 管主机秘密与 Caddy，Doco-CD 独占应用容器生命周期。

输入 `ZITADEL_GITOPS_URL`、`ZITADEL_GITOPS_SHA` 和 GitOps 提供的三个镜像 digest；
使用独立 reconciler、数据卷、localhost 控制端口与 `target: zitadel`，
不能误读默认 Web SaaS 配置。Secrets 从 Vault 会话注入，不使用生产长期管理 token。
`ZITADEL_LOGIN_SESSION_COOKIE_SECRET` 至少 32 字符；masterkey 已存在时禁止静默轮换。
主机秘密 root/0600，PAT 和 PostgreSQL 数据不随重部署清理。

GitOps Compose 使用原生 `start-from-init`，初始化失败必须使应用不健康。
等待实际 API/Login 镜像 digest 和 health，再由上层验证公网 OIDC。
旧直接 Compose 只能显式 `-e zitadel_deployment_mode=compose`；检测到旧 Compose
配置时 Doco-CD 入口拒绝启动，需另行规划无损接管，不能自动删除旧栈。
