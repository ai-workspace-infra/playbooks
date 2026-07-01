# Site Migration Toolkit (基于 AI 驱动的站点级自动化迁移容灾解决方案)

**Site Migration Toolkit** 绝不仅仅是一条普通的 CI/CD 流水线，而是一套面向现代化基础设施和高并发应用集群的**开源级自动化搬站与容灾解决方案**。

在面对跨云、跨主机迁移等高风险、重载场景时，它彻底摒弃了传统的本地打包中转模式，创新性地依托 **S3 对象存储作为高速流式传输隧道**，并深度结合 **HashiCorp Vault 动态 JWT 鉴权**，实现全链路的“零本地磁盘占用”与“零密钥明文落盘”。无论是 TB 级体量的 Gitea 源码库、重型 PostgreSQL 业务数据库集群、复杂的 Docker 容器镜像集，还是 AI 应用的持久化工作区数据，本工具包均能提供安全、智能、极速的“平滑数据漂移”。

## 🌟 核心理念与特性 (Core Features)

- 🤖 **AI 驱动的架构自进化**：不仅迁移数据，更通过大模型自动化生成迁移策略、动态渲染复杂配置文件（如跨域 Caddy Domain 级联重写）。
- 🌊 **极致的流式中转 (Zero-Disk Overhead)**：彻底消灭由于 `tar` 打包引发的“源服务器磁盘打爆”事故。全程基于 Linux Pipes 与 S3 底层网络，导出数据瞬间上云，目标端“边下边解”，**对服务器磁盘容量实现零附加要求**。
- 🛡️ **Vault 零信任安全底座**：彻底告别 `.env` 或静态密钥配置文件。在迁移时瞬间向 `HashiCorp Vault` 发起 JWT 短期认证，提取 S3 AK/SK 凭证放入运行时内存，任务结束凭证即焚。
- ⚡ **原生增量与断点续传**：深度整合底层 `aws s3 sync` 增量比对协议，在动辄几十 GB 大文件或跨国弱网环境中，天然免疫网络闪断。
- 📦 **Docker 镜像真空打包**：针对目标集群可能遭遇的镜像拉取限流（如 DockerHub Rate Limit），支持在源端一键 `docker save` 存活镜像并直推 S3，在无外网环境下亦可极速冷启动。

## 🛠️ 技术栈与生态圈 (Technology Stack)

- **核心编排引擎**: Ansible / Ansible Vault
- **安全与身份网关**: HashiCorp Vault (动态 JWT / KV2)
- **底层对象存储隧道**: AWS S3 (或兼容的 MinIO / OSS / OBS)
- **CLI/自动化底座**: AWS CLI v2 / Shell Pipelines (`gzip` / `gunzip` stream)
- **首批支持开箱即用的技术栈**:
  - PostgreSQL (通过 `pg_dump` 管道)
  - Gitea Server (含静态归档向 S3 原生引擎的无缝切库)
  - Docker Containers (容器热备份)
  - Caddy / APISIX (网关配置自适应渲染)

---
## XWorkmate Bridge Distributed VPN

The bidirectional WireGuard-over-VLESS transport for the two XWorkmate bridge
nodes is deployed by:

```bash
ansible-playbook -i inventory.ini vpn-wireguard-over-vless.yml
```

The implementation uses split bridge groups (`xworkmate_bridge` and
`cn_xworkmate_bridge`) under `xworkmate_bridge_distributed`, stores private keys
and the shared management-side Xray UUID in `https://vault.svc.plus`, and keeps
the host's default `xray.service` untouched. The runbook lives in
[`roles/vhosts/xworkmate_bridge_distributed_vpn/README.md`](/Users/shenlan/workspaces/cloud-neutral-toolkit/playbooks/roles/vhosts/xworkmate_bridge_distributed_vpn/README.md).

## Cloud Dev Desktop

The cloud dev desktop flow lives here as two playbooks:

1. `bootstrap_cloud_dev_desktop.yml`
2. `destroy_cloud_dev_desktop.yml`

`bootstrap_cloud_dev_desktop.yml` now includes the create/bootstrap/verify sequence in one entry point. The control-plane repo calls these playbooks from `../playbooks`.

## Traffic Billing Stack

The traffic billing stack now has a single aggregate playbook:

`deploy_svc_plus_core_services_stack.yml`

It orchestrates these existing playbooks in dependency order:

1. `deploy_billing_service.yml`
2. `deploy_xworkmate_bridge_vhosts.yml`
3. `deploy_xray_exporter.yml`
4. `deploy_agent_svc_plus.yml`
5. `deploy_accounts_svc_plus.yml`
6. `deploy_stunnel-client.yml`
7. `deploy_apisix.yml`
8. `deploy_console_svc_plus.yml`

### Full stack deploy

```bash
cd /Users/shenlan/workspaces/cloud-neutral-toolkit/playbooks
export INTERNAL_SERVICE_TOKEN=...
export DATABASE_URL=postgres://...
export FRONTEND_IMAGE=ghcr.io/x-evor/dashboard:latest
export STACK_TARGET_HOST=jp_xhttp_contabo_host
export console_service_sync_dns=true
ansible-playbook -i inventory.ini deploy_svc_plus_core_services_stack.yml
```

`STACK_ENV_FILE=./.env` is optional. Use it when you want the aggregate playbook to read a local `.env` file; GitHub Actions or other CI runners can skip it and pass values with `-e` instead.

### Deploy to one target host directly

Use `STACK_TARGET_HOST` to override the stack host groups when you want all services to target the same inventory host. For console-only runs, use Ansible's `-l jp_xhttp_contabo_host` limit instead of a separate host variable, and keep `console_service_sync_dns=true` if you want DNS reconciliation.

```bash
cd /Users/shenlan/workspaces/cloud-neutral-toolkit/playbooks
export STACK_TARGET_HOST=jp_xhttp_contabo_host
export INTERNAL_SERVICE_TOKEN=...
export DATABASE_URL=postgres://...
export FRONTEND_IMAGE=ghcr.io/x-evor/dashboard:latest
export console_service_sync_dns=true
ansible-playbook -i inventory.ini -l jp_xhttp_contabo_host deploy_svc_plus_core_services_stack.yml
```

### Deploy only selected services

Use `STACK_SERVICES` with a comma-separated list:

- `billing-service`
- `xworkmate-bridge`
- `xray-exporter`
- `agent`
- `accounts`
- `stunnel-client`
- `apisix`
- `console`

```bash
cd /Users/shenlan/workspaces/cloud-neutral-toolkit/playbooks
export STACK_TARGET_HOST=jp-xhttp-contabo.svc.plus
export STACK_SERVICES=xray-exporter,billing-service,agent,xworkmate-bridge
export INTERNAL_SERVICE_TOKEN=...
export DATABASE_URL=postgres://...
ansible-playbook -i inventory.ini -l jp_xhttp_contabo_host deploy_svc_plus_core_services_stack.yml
```

### Notes

- `accounts` and `console` still use their existing role contracts.
- `console` requires `FRONTEND_IMAGE` because the target host only does pull-only compose deployment.
- `console` now writes a Caddy fragment named like `<server-name>-<release_id>-<hostname>-<domain>.caddy` instead of managing the Caddy service container itself.
- `billing-service` requires `DATABASE_URL`.
- `xray-exporter` and `agent` require `INTERNAL_SERVICE_TOKEN`.
- `xworkmate-bridge` accepts `XWORKMATE_BRIDGE_HOSTS`, and also follows `STACK_TARGET_HOST` when you want to deploy the whole stack to one host.

### Deploy console to a specific host and sync DNS

`deploy_console_svc_plus.yml` now accepts `console_service_sync_dns=true` to rebuild and reconcile DNS records after deployment. For host selection, use Ansible's `-l jp_xhttp_contabo_host` limit.

Example:

```bash
cd /Users/shenlan/workspaces/cloud-neutral-toolkit/playbooks
ansible-playbook -i inventory.ini deploy_console_svc_plus.yml \
  -e console_service_sync_dns=true \
  -e FRONTEND_IMAGE=ghcr.io/x-evor/dashboard:latest
```
