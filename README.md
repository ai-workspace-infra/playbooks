# ai-workspace-infra / playbooks

面向 **svc.plus 平台**与 **AI Workspace** 的 Ansible 控制面仓库。

这个仓库不是单一工具，而是一个**按角色（role）组织、按场景（playbook）编排**的主机自动化集合：
从裸机基线、K3s/GPU 集群、可观测性栈、网关与 DNS，到业务服务上线、跨云搬站与容灾，全部以声明式 role 沉淀，用顶层 playbook 拼装成可重复执行的场景入口。

| 规模 | 数量 |
| --- | --- |
| 顶层 playbook（`*.yml` / `*.yaml`） | 137 |
| Role 命名空间 | 5（`vhosts/` `docker/` `charts/` `host/` + 顶层独立 role） |
| Role 总数 | 184（`vhosts` 89 / `charts` 50 / `docker` 28 / 顶层 16 / `host` 1） |
| 带 README 的 role | 62 |

---

## 快速开始

```bash
# 1. 依赖
ansible --version          # ansible-core ≥ 2.14
aws --version              # 迁移/备份类 playbook 需要 AWS CLI v2

# 2. 清单：默认 ./inventory.ini（见 ansible.cfg）
grep -E '^\[' inventory.ini

# 3. 连通性
ansible -i inventory.ini all -m ping

# 4. 干跑任意场景
ansible-playbook -i inventory.ini <playbook>.yml --check --diff
```

`ansible.cfg` 已固化的默认行为：`inventory = ./inventory.ini`、`host_key_checking = False`、
fact 缓存落 `/tmp/ansible_facts`（1h）、开启 `pipelining` 与 `profile_tasks,timer` 回调。
无需额外 `-i` 也能跑，但**跨仓库调用时请显式带上 `-i inventory.ini`**。

---

## 仓库结构

| 路径 | 作用 |
| --- | --- |
| `*.yml` / `*.yaml` | 顶层场景入口（playbook），一个文件 = 一个可执行场景 |
| `roles/vhosts/` | **宿主机原生交付**：systemd 服务、on-host 配置渲染、内核/网络调优 |
| `roles/docker/` | **Docker Compose 交付**：渲染 `docker-compose.yaml.j2` 并拉起单机服务栈 |
| `roles/charts/` | **Kubernetes / Helm 交付**：以 Helm release 形式投放到集群 |
| `roles/host/` | 宿主机级共享物料（目前仅 `stunnel-certs`） |
| `roles/grafana-dashboard/` | Grafana 面板 JSON 资产（非可执行 role） |
| `roles/README.md` | 角色分层规划：Ansible role vs. Helm chart 的边界约定 |
| `group_vars/` `host_vars/` | 全局与主机变量；`group_vars/all/vault_s3.yml` 承载 S3/Vault 凭据 |
| `inventory/` | 动态清单：`terraform_cmdb.py`（Terraform CMDB）、`hosts.ini` |
| `vars/` | DNS 记录等数据集（`dns_records_svc_plus.yaml`、`cloudflare_svc_plus_dns.yml`） |
| `docs/` | 设计文档、迁移记录、任务与用例（`k3s-role-map.md`、`vault-secrets-dataflow-design.md` 等） |
| `scripts/` | 非 Ansible 的一次性/旁路脚本（安装器、证书轮转、网络排查） |
| `skills/` | 仓库协作约定：`config-as-code-Spec`、`release-branch-policy` |
| `.github/workflows/` | 域名级 CD 流水线 + release PR 校验 |

### 三种交付形态

同一个组件可能在三个命名空间下各有一份 role，**不是重复，是交付形态不同**，按目标环境选择：

```
roles/vhosts/postgres      → 直接装在宿主机上，systemd 托管
roles/docker/postgres      → 单机 Docker Compose 栈
roles/charts/postgresql    → K8s 集群内 Helm release
```

`roles/README.md` 给出的边界规则：**与宿主机强耦合的配置走 Ansible role，跑在 Pod 里的工作负载走 Helm chart。**

---

## 角色分类（roles）

> 下表按能力域归类；`vhosts/*` 省略前缀，`docker/*` 与 `charts/*` 显式标注。
> ⚠️ 标记表示 role 目录已建但 `tasks/main.yml` 仍是 placeholder，见文末[实现状态](#实现状态)。

### 1. 主机基线与安全

| Role | 说明 |
| --- | --- |
| `common` | 通用初始化：包源、基础工具、目录布局 |
| `kernel_tuning` | 内核参数、文件句柄、网络栈调优 |
| `docker` / `docker/container_runtime` | Docker Engine 与容器运行时（含 GPU runtime 集成） |
| `python3` / `nodejs` | 语言运行时基线 |
| `firewall` | 防火墙规则下发 |
| `ssh-trust` | 主机间 SSH 互信 |
| `harden_ssh_root_key_only` | SSH 加固：仅 root + 密钥登录 |
| `readonly_ssh_user` | 只读审计账号下发 |
| `cloud_cli_prereqs` | 云厂商 CLI 前置依赖 |
| `validation` | 部署前后的主机态校验 |
| `network_info` | 网络事实采集 |

### 2. Kubernetes 与集群

| Role | 说明 |
| --- | --- |
| `k3s` / `k3s-cluster` / `k3s-cluster-server` / `k3s-cluster-agent` | 单机与多节点 K3s 安装 |
| `k3s-addon` / `k3s_platform_addon` | K3s 平台插件层 |
| `k3s_platform_bootstrap` | 单节点 K3s GitOps 平台引导 |
| `k3s-reset` | K3s 环境重置 |
| `k8s-node` | 通用 Kubernetes 节点引导 |
| `sealos-k8s` / `sealos_cluster` | 基于 Sealos 的集群安装 |
| `cni_cilium` | Cilium CNI |
| `gpu-k8s` / `gpu-k8s-reset` | GPU 节点的 K8s 装配与回滚 |
| `charts/metrics-server` `charts/kubernetes-dashboard` `charts/chaos-mesh` | 集群基础组件 |

参考：[`docs/k3s-role-map.md`](docs/k3s-role-map.md)

### 3. GPU / AI 推理与 Agent 运行时

| Role | 说明 |
| --- | --- |
| `charts/gpu-operator` / `charts/nvidia_gpu_operator` | NVIDIA GPU Operator |
| `charts/ray_cluster` / `charts/ray_service` | Ray 集群与服务 |
| `charts/vllm_runtime` / `charts/vllm_service` | vLLM 推理运行时与服务 |
| `litellm` | LiteLLM 多模型网关 |
| `ai_agent_runtime` | AI Agent 运行时装配 |
| `agent_skills` | Agent skills 分发 |
| `ai-workspace` | AI Workspace 主体服务 |
| `acp_vhosts` `acp_server_codex` `acp_server_gemini` `acp_server_hermes` `acp_server_opencode` | ACP 协议各家 agent 适配端 |
| `qmd` | QMD 扩展记忆 |
| `x_memory_hub` | X Memory Hub |
| `charts/sglang` ⚠️ `charts/vllm` ⚠️ `charts/inference-gateway` ⚠️ `charts/embedding-service` ⚠️ | 规划中的推理链路组件 |

### 4. 网关、证书与 DNS

| Role | 说明 |
| --- | --- |
| `caddy` | Caddy 反向代理；各服务 role 只投放 `conf.d` 片段，不接管 Caddy 容器 |
| `nginx` / `nginx-proxy` / `OpenResty` | Nginx 系网关 |
| `apisix_service` | APISIX 网关 |
| `cert-manager` | 证书签发与续期（[`docs/cert-manager-arch.md`](docs/cert-manager-arch.md)） |
| `gateway_openclaw` | OpenClaw 网关 vhost |
| `cloudflare_dns` | Cloudflare DNS 记录对账 |
| `alicloud_dns_record` | 阿里云 DNS 记录（[`docs/alicloud_dns_sync.md`](docs/alicloud_dns_sync.md)） |
| `host/stunnel-certs` | stunnel 证书物料 |

`caddy_enabled` / `caddy_config_dir` 在 `group_vars/all.yml` 中按 OS 自动切换（Linux `/etc/caddy`，macOS Homebrew 路径），可用 `-e caddy_enabled=` 强制覆盖。

### 5. 可观测性

| Role | 说明 |
| --- | --- |
| `prometheus` / `charts/prometheus` | 指标采集 |
| `grafana` / `docker/grafana` | 可视化；面板 JSON 在 `roles/grafana-dashboard/` |
| `openobserve` | OpenObserve 日志/指标后端 |
| `otel-collector` | OpenTelemetry Collector |
| `alloy` / `vector-agent` / `promtail-agent` / `telegraf` | 采集侧 agent |
| `node_exporter` / `process_exporter` / `blackbox_exporter` | Exporter 三件套 |
| `xray-exporter` | Xray 流量指标导出（计费链路上游） |
| `deepflow_agent` / `charts/deepflow` | DeepFlow eBPF 观测 |
| `alerting` | 告警配置（当前以 Grafana 原生告警为准） |
| `prometheus-transfer` | 指标转发/迁移 |
| `docker/observability-server` / `charts/observability-server` / `charts/observability-agent` | 观测服务端与 agent 的容器/Helm 形态 |
| `charts/splunk-otel-collector` `charts/node-exporter` | 集群内采集 |
| `docker/OpenObserve` ⚠️ `docker/Tempo` ⚠️ `docker/VictoriaLogs` ⚠️ `docker/VictoriaMetrics` ⚠️ `docker/otel` ⚠️ `docker/loki` ⚠️ `charts/loki` ⚠️ `charts/tempo` ⚠️ `charts/prometheus-stack` ⚠️ `charts/openobserve` ⚠️ | 已建目录、待实现 |

### 6. 网络、VPN 与代理

| Role | 说明 |
| --- | --- |
| `wireguard-gateway` / `wireguard-client` | WireGuard hub / site |
| `xworkmate_bridge` | XWorkmate 桥接节点 |
| `xworkmate_bridge_distributed_vpn` | WireGuard over VLESS 双向隧道（私钥与共享 Xray UUID 存 `vault.svc.plus`，不动宿主机默认 `xray.service`） |
| `tky-proxy` | 东京出口代理 |
| `docker/stunnel-client` / `docker/stunnel-server` | PostgreSQL 等内网服务的 TLS 隧道 |
| `vpn-overlay` ⚠️ | 目录占位，实际逻辑在 `vpn-overlay-*.yaml` playbook 中 |

Runbook：[`roles/vhosts/xworkmate_bridge_distributed_vpn/README.md`](roles/vhosts/xworkmate_bridge_distributed_vpn/README.md)

### 7. 数据与存储

| Role | 说明 |
| --- | --- |
| `postgres` / `postgresql_service` / `docker/postgres` / `charts/postgresql` | PostgreSQL 三种交付形态 |
| `Redis` / `charts/redis` | Redis |
| `charts/mysql` / `charts/clickhouse` | MySQL / ClickHouse |
| `docker/harbor` / `charts/harbor` | 镜像仓库 Harbor |
| `zot` | 轻量 OCI registry |
| `charts/minio` ⚠️ `charts/iceberg-bucket` ⚠️ `charts/feast` ⚠️ `charts/mlflow` ⚠️ `charts/trino` ⚠️ `charts/kafka-cluster` ⚠️ `charts/redpanda` ⚠️ `charts/spark-operator` ⚠️ `charts/flink-operator` ⚠️ | 数据平台规划位，见 `roles/README.md` 的五层能力矩阵 |

### 8. 身份与密钥

| Role | 说明 |
| --- | --- |
| `vault` | HashiCorp Vault 服务部署 |
| `secret-manger` | 密钥下发（注意：目录名保留了历史拼写） |
| `docker/keycloak` / `charts/keycloak` | Keycloak |
| `docker/zitadel` / `docker/zitadel_legacy` | Zitadel（含遗留版本） |
| `charts/openldap` | OpenLDAP |

数据流设计：[`docs/vault-secrets-dataflow-design.md`](docs/vault-secrets-dataflow-design.md)

### 9. svc.plus 业务服务

| Role | 说明 |
| --- | --- |
| `accounts_service` | accounts.svc.plus |
| `console_service` | console.svc.plus（仅 pull-only compose 部署，写 Caddy 片段而非托管 Caddy） |
| `docs_service` | docs.svc.plus |
| `billing-service` | 计费服务（依赖 `DATABASE_URL`） |
| `agent-proxy` | Agent Proxy（上游实现为 agent.svc.plus） |
| `xcontrol_server` / `docker/XControl` | XControl 控制端 |
| `web_saas_host_config` | web-saas 宿主机配置，部署权交给 Doco-CD |
| `nextjs` / `docker/neurapress` / `modern_it_history` | 站点类应用 |
| `chasquid` / `dovecot` | 自建邮件收发 |

### 10. 交付与 GitOps

| Role | 说明 |
| --- | --- |
| `Doco-CD` | Compose 侧的 GitOps 交付器 |
| `action-runner` | 自托管 GitHub Actions runner |
| `gitea` | Gitea 服务 |
| `github`（顶层 role） | GitHub 仓库/分支保护治理 |
| `charts/argo-server` / `charts/app` / `charts/helm-repos` | Argo CD 与 Helm 源 |
| `charts/jenkins` / `charts/gitlab` / `charts/chartmuseum` / `charts/flagger-loadtester` | CI 与发布配套 |

### 11. 云开发桌面

| Role | 说明 |
| --- | --- |
| `dev_desktop_common` | 桌面通用基线 |
| `dev_desktop_debian_kde` / `dev_desktop_fedora_gnome` / `dev_desktop_windows` | 三种发行版/系统的桌面镜像 |
| `azure_dev_desktop_lifecycle` / `gcp_dev_desktop_lifecycle` | Azure / GCP 上的实例生命周期 |
| `cloud_vm_request_validate` | 申请参数校验（创建前置闸门） |
| `cloud_vm_inventory_emit` | 创建后回写 Ansible 清单 |
| `xfce_desktop_minimal_runtime` / `gnome_xrdp_minimal` / `plasma_xrdp_minimal` | 最小化桌面运行时 |
| `remote_desktop_xrdp_server` | XRDP 远程桌面服务端 |

### 12. 备份、迁移与容灾

| Role | 说明 |
| --- | --- |
| `site_migration`（顶层 role） | 站点级搬迁核心：`extract.yml` 导出 / `load.yml` 导入 |
| `macos_migration` | macOS 开发机的应用、Homebrew、目录跨机迁移 |

搬站链路的设计取向：以 **S3 对象存储作为流式中转隧道**，源端 `pg_dump`/`tar` 直接通过 Linux pipe 推上 S3，目标端边下边解，**不在本地磁盘落中间产物**；S3 凭据在运行时通过 Vault JWT 短期认证取得，任务结束即失效。增量同步复用 `aws s3 sync` 的比对协议，弱网可断点续传。

---

## Playbook 分类

### 主机基线与账号安全

| Playbook | 场景 |
| --- | --- |
| `common_setup.yml` | 通用基础设施初始化 |
| `setup-docker.yml` / `setup-python3.yml` / `setup-nodejs.yml` | 运行时安装 |
| `harden_ssh_root_key_only.yml` | 全量清单 SSH 加固 |
| `create_audit_user.yml` / `create_readonly_ssh_user.yml` | 审计与只读账号 |
| `setup-root-authorized-key.yml` | 追加本地公钥到 root |

参考：[`docs/tldr-ssh-security.md`](docs/tldr-ssh-security.md)

Stripe 新环境套餐初始化：[`docs/tldr-stripe-billing-catalog.md`](docs/tldr-stripe-billing-catalog.md)

### 云开发桌面

| Playbook | 场景 |
| --- | --- |
| `bootstrap_cloud_dev_desktop.yml` | 单入口完成 create → bootstrap → verify |
| `destroy_cloud_dev_desktop.yml` | 销毁桌面基础设施 |
| `gnome_xrdp_minimal.yaml` / `plasma_xrdp_minimal.yaml` / `setup-xfce-xrdp.yaml` | 最小桌面 + XRDP |

控制面仓库通过 `../playbooks` 相对路径调用上述入口。

### Kubernetes 平台

| Playbook | 场景 |
| --- | --- |
| `k3s-cluster.yaml` / `setup-k3s-node.yaml` / `setup-k8s-node.yaml` | 集群与节点安装 |
| `k3s_platform_bootstrap_with_gitops.yml` | 单节点 K3s + GitOps 引导 |
| `k3s_platform_addon.yml` | 平台插件层 |
| `k3s_reset.yml` | 重置 |
| `gpu_k8s_init.yml` / `gpu_k8s_reset.yml` | GPU 节点集群装配与回滚 |

### GPU 推理链路（分阶段）

按序执行，或用 `gpu_inference_site.yml` 一次跑完：

1. `gpu_inference_01_prepare.yml` — 主机环境准备
2. `gpu_inference_02_sealos.yml` — Sealos 安装 Kubernetes
3. `gpu_inference_03_gpu_operator.yml` — NVIDIA GPU Operator
4. `gpu_inference_04_ray.yml` — Ray 集群
5. `gpu_inference_05_vllm.yml` — vLLM 推理服务

### svc.plus 核心业务栈

聚合入口 `deploy_svc_plus_core_services_stack.yml`，按依赖顺序编排下列 playbook：

`deploy_billing_service.yml` → `deploy_xworkmate_bridge_vhosts.yml` → `deploy_xray_exporter.yml` → `deploy_xray_proxy_server.yml` → `deploy_accounts_svc_plus.yml` → `deploy_stunnel-client.yml` → `deploy_apisix.yml` → `deploy_console_svc_plus.yml`

```bash
export INTERNAL_SERVICE_TOKEN=...
export DATABASE_URL=postgres://...
export FRONTEND_IMAGE=ghcr.io/x-evor/dashboard:latest
export STACK_TARGET_HOST=jp_xhttp_contabo_host
export console_service_sync_dns=true
ansible-playbook -i inventory.ini deploy_svc_plus_core_services_stack.yml
```

**栈级变量**

| 变量 | 作用 |
| --- | --- |
| `STACK_ENV_FILE` | 可选。指定本地 `.env` 供聚合 playbook 读取；CI 里可省略，直接用 `-e` 传值 |
| `STACK_TARGET_HOST` | 把全部服务的主机组覆盖为同一台清单主机 |
| `STACK_SERVICES` | 逗号分隔的子集：`billing-service` `xworkmate-bridge` `xray-exporter` `agent` `accounts` `stunnel-client` `apisix` `console` |

只发部分服务：

```bash
export STACK_TARGET_HOST=jp-xhttp-contabo.svc.plus
export STACK_SERVICES=xray-exporter,billing-service,agent,xworkmate-bridge
ansible-playbook -i inventory.ini -l jp_xhttp_contabo_host deploy_svc_plus_core_services_stack.yml
```

**各服务的硬依赖**

- `billing-service` 需要 `DATABASE_URL`
- `xray-exporter`、`agent` 需要 `INTERNAL_SERVICE_TOKEN`
- `console` 需要 `FRONTEND_IMAGE`（目标机只做 pull-only compose 部署）；它写出的是形如 `<server-name>-<release_id>-<hostname>-<domain>.caddy` 的片段，不接管 Caddy 服务容器
- `xworkmate-bridge` 接受 `XWORKMATE_BRIDGE_HOSTS`，也遵循 `STACK_TARGET_HOST`
- 单服务限定主机用 Ansible 原生的 `-l <host>`，不要另造主机变量

Console 单独发布并对账 DNS：

```bash
ansible-playbook -i inventory.ini deploy_console_svc_plus.yml \
  -e console_service_sync_dns=true \
  -e FRONTEND_IMAGE=ghcr.io/x-evor/dashboard:latest
```

扩展服务：`deploy_svc_plus_extended-services.yml`、`deploy_docs_svc_plus.yml`、`deploy_postgresql_svc_plus.yml`、`deploy_apisix_svc.plus.yaml`、`deploy_xray_proxy_server.yml`

### 可观测性

| Playbook | 场景 |
| --- | --- |
| `deploy_observability.yml` | Xray Exporter + Vector Agent |
| `deploy_observability_agent.yml` | 采集 agent |
| `deploy_monitor_server.yml` / `deploy_tiny_monitor_server_vhost.yml` | 监控服务端（完整 / 精简） |
| `deploy_exporters_vhosts.yml` / `deploy_node_process_exporters.yml` / `deploy_blackbox_exporters_vhosts.yml` | Exporter 下发 |
| `deploy_vhosts_otel-collector.yml` / `deploy_otel_docker.yaml` | OTel Collector |
| `deploy_grafana_docker.yaml` / `deploy_OpenObserve_docker.yaml` / `deploy_Tempo_docker.yaml` / `deploy_VictoriaLogs_docker.yaml` / `deploy_VictoriaMetrics_docker.yaml` | 观测后端的 Docker 形态 |
| `deploy_xray_exporter.yml` | Xray 流量指标 |

### 网络与 VPN

| Playbook | 场景 |
| --- | --- |
| `vpn-wireguard-over-vless.yml` | XWorkmate 双向 WireGuard over VLESS（`xworkmate_bridge` + `cn_xworkmate_bridge` 两个 group，聚合在 `xworkmate_bridge_distributed`） |
| `vpn-wireguard-hub.yaml` / `vpn-wireguard-site.yaml` | WireGuard hub / site |
| `vpn-overlay-vxlan-hub.yaml` / `vpn-overlay-vxlan-site.yaml` / `vpn-overlay-dnat.yaml` | VXLAN overlay 与 DNAT |
| `vpn-xray-hub.yaml` / `vpn-xray-client.yaml` / `vpn-xray-tproxy.yaml` | Xray hub / client / 透明代理 |
| `init_vpn_gateway.yml` | VPN 网关初始化 |
| `deploy_stunnel-server.yml` / `deploy_stunnel-client.yml` | PostgreSQL TLS 隧道两端 |

### 数据库

| Playbook | 场景 |
| --- | --- |
| `create_databases_and_users.yml` | 按需创建独立库与账号 |
| `initialize-web-saas-schemas.yml` | web-saas 基线 schema |
| `write_passwords_to_vault.yml` | 数据库口令写入 Vault |
| `setup-postgres-standalone.yaml` / `deploy_postgre_vhosts.yml` | 单机 PostgreSQL |
| `deploy_redis_vhosts.yml` | Redis |

### 网关、站点与 DNS

| Playbook | 场景 |
| --- | --- |
| `setup-caddy.yml` / `deploy_nginx_vhosts.yml` / `deploy_openresty_vhosts.yml` / `deploy_apisix.yml` | 网关 |
| `update_cloudflare_dns.yml` / `update_site_dns.yml` | Cloudflare DNS 对账 |
| `alicloud_dns_record.yml` / `alicloud_dns_sync.yml` | 阿里云 DNS |
| `setup-web-saas-domain.yml` / `setup-agent-proxy-domain.yml` / `setup-open-platform-domain.yml` | 域名接入（对应 `.github/workflows/*-domain-cd.yaml`） |

### 备份 / 迁移 / 容灾

同一套 **backup / migrate / restore** 三件套按业务域复制：

| 业务域 | backup | migrate | restore |
| --- | --- | --- | --- |
| 通用站点 | `backup_site.yml` | `migrate_site.yml` | `restore_site.yml` |
| AI Workspace | `ai-workspace-backup.yml` | `ai-workspace-migrate.yml` | `ai-workspace-restore.yml` |
| Agent Proxy | `agent-proxy-backup.yml` | `agent-proxy-migrate.yml` | `agent-proxy-restore.yml` |
| Web SaaS | `web-saas-backup.yml` | `web-saas-migrate.yml` | `web-saas-restore.yml` |
| 基础平台 | `infra-platform-backup.yml` | `infra-platform-migrate.yml` | `infra-platform-restore.yml` |
| macOS 开发机 | `setup-macos-migration-backup.yml` | `setup-macos-migration-migrate.yml` | `setup-macos-migration-restore.yml` |

`migrate` 走 extract → load 两阶段；`dynamic_inventory.yml` 用于在运行期动态加入迁移主机。
另有 `setup-macos-migration-google-drive-sync.yml`（Obsidian Vault → Google Drive）。

### AI Workspace

| Playbook | 场景 |
| --- | --- |
| `setup-ai-workspace-all-in-one.yml` | 一体化部署（[文档](docs/setup-ai-workspace-all-in-one.md)） |
| `setup-ai-workspace-preflight.yml` | 运行模式校验（前置闸门） |
| `setup-ai-workspace-runtime.yml` / `setup-ai-workspace-rootless.yml` | 运行时 / rootless 模式 |
| `setup-ai-agent-skills.yml` | Agent skills 下发 |
| `deploy_agent_hermes.yml` / `deploy_acp_codex_vhosts.yml` / `deploy_acp_gemini_vhosts.yml` / `deploy_acp_opencode_vhosts.yml` | 各家 ACP agent 适配端 |
| `deploy_QMD.yml` / `deploy_x_memory_hub.yml` | 扩展记忆组件 |
| `setup-xworkspace-console.yaml` / `xworkspace_console_macos.yml` | XWorkspace 控制台（含 macOS launchd plist） |

参考：[`docs/ai-workspace-runtime-delivery-plan.md`](docs/ai-workspace-runtime-delivery-plan.md)

### 平台组件与其他

`setup-vault.yaml`（Vault KMS）、`setup-Doco-CD.yaml`、`setup-litellm.yaml`（[文档](docs/litellm-gateway-deployment.md)）、
`deploy_zitadel_docker.yaml`、`deploy-docker-keycloak.yml`、`deploy-docker-harbor.yml`、
`deploy_action_runner.yml`、`apply-branch-protection.yml`、
`deploy_xcontrol_server._vhosts.yml`、`deploy_xcontrol_dashboard.yml`、
`deploy_modern_it_history.yml`、`deploy_neurapress_docker.yaml`、`setup-nextjs.yml`、`deploy_gateway_openclaw.yml`

---

## 约定

**清单与分组** — `inventory.ini` 按「地域 + 角色」分组：`jp_xhttp_contabo_host`、`us_xhttp_host`、`cn_front_host`、`tky_proxy_host`、`jp_k3s_vultr_host`，以及服务组 `agent_proxy`、`billing_service`、`accounts`、`docs`、`apisix`、`postgresql`、`k3s`、`observability_hosts`、`xray_exporter`。`xworkmate_bridge_distributed` 是 `xworkmate_bridge` + `cn_xworkmate_bridge` 的父组。动态清单走 `inventory/terraform_cmdb.py`。

**密钥** — 不落静态密钥文件。S3/DB 凭据在运行时经 Vault（`https://vault.svc.plus`）JWT 短期认证取得，仅驻留内存。仓库启用 gitleaks（`.gitleaks.toml` / `.gitleaksignore`）。

**分支** — `main` 是 preview 分支，`release/*` 是生产发布线，只接受 release manager 本地 cherry-pick，禁止 force-push、要求线性历史。完整策略见 [`skills/release-branch-policy/SKILL.md`](skills/release-branch-policy/SKILL.md)，PR 校验由 `.github/workflows/validate-release-pr.yml` 执行。

**Role vs. Chart** — 新增组件前先读 [`roles/README.md`](roles/README.md)：宿主机耦合的配置进 `roles/vhosts`（或 `roles/docker`），Pod 内工作负载进 `roles/charts`。

---

## 实现状态

目录已建但 `tasks/main.yml` 仍为 placeholder 的 role —— **执行它们只会打印一条 debug 消息，不会产生任何变更**：

- `roles/charts/`（21 个）：`embedding-service` `feast` `flink-operator` `grafana` `iceberg-bucket` `inference-gateway` `kafka-cluster` `loki` `minio` `mlflow` `nvidia-operator` `openobserve` `postgres` `prometheus-stack` `ray-cluster` `redpanda` `sglang` `spark-operator` `tempo` `trino` `vllm`
- `roles/docker/`（16 个）：`OpenObserve` `Tempo` `VictoriaLogs` `VictoriaMetrics` `clickhouse` `embedding-service` `kafka` `loki` `minio` `mlflow` `otel` `ray` `redpanda` `sglang` `trino` `vllm`
- `roles/vhosts/`（4 个，无 `tasks/` 目录）：`HAProxy` `alicloud_dns_sync` `docker-compose` `vpn-overlay`

注意 `charts/` 下存在同名近义目录（`ray-cluster` ⚠️ vs `ray_cluster` ✅、`vllm` ⚠️ vs `vllm_service` ✅、`nvidia-operator` ⚠️ vs `nvidia_gpu_operator` ✅、`postgres` ⚠️ vs `postgresql` ✅），**连字符版本是占位，下划线版本是实现**，引用时不要选错。

---

## CI

| Workflow | 触发场景 |
| --- | --- |
| `domain-cd.yaml` | 通用域名交付 |
| `web-saas-domain-cd.yaml` / `agent-proxy-domain-cd.yaml` / `ai-workspace-domain-cd.yaml` / `open-platform-domain-cd.yaml` | 各业务域的域名级 CD |
| `validate-release-pr.yml` | release 分支 PR 策略校验 |

---

## License

见 [LICENSE](LICENSE)。
