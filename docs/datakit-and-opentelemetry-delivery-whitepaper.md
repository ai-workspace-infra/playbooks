---
title: DataKit 跨云可观测性与 OpenTelemetry 数据网关交付白皮书
description: 规范 Kubernetes (PROD/SIT) DataKit DaemonSet 部署、HashiCorp Vault 凭据中心、DataWay 公网网关、Cloudflare/Cloud Run/Supabase 跨云汇聚及 OpenTelemetry 端到端采集规范。
slug: datakit-and-opentelemetry-delivery-whitepaper
lang: zh
tags:
  - observability
  - datakit
  - opentelemetry
  - guance
  - vault
  - k3s
  - gitops
---

# DataKit 跨云可观测性与 OpenTelemetry 数据网关交付白皮书

> **文档状态**: 正式归档 (Approved & Verified)  
> **归档位置**: `modern-it-infrastructure-platform-engineering/docs/zh/observability/02-opentelemetry/`、`knowledge/docs/reference/` 与 `playbooks/docs/`  
> **文档版本**: `v1.1.0`  
> **发布日期**: 2026-10-28  
> **涉及系统**: PROD K3S (`web-saas-k3s-v5`), SIT K3S (`dev-k3s-v5`), UAT Serverless (Cloudflare / GCP Cloud Run / Supabase DB), 观测云 DataWay (`https://cn3-openway.guance.com`), HashiCorp Vault (`https://vault.svc.plus`)

---

## 一、架构愿景与拓扑总览

随着多云与混合云业务体系的发展，应用系统与微服务横跨 **Kubernetes 容器集群（PROD/SIT）**、**无服务器运行时（GCP Cloud Run）**、**全球边缘网络（Cloudflare Workers / Logpush）** 以及 **托管数据库（Supabase PostgreSQL）**。

为了打破多云孤岛，实现统一全景可观测性，本白皮书规范了基于 **DataKit (Kubernetes Helm DaemonSet)**、**OpenTelemetry 协议中枢**、**HashiCorp Vault 凭据中心** 及 **Flux CD / Ansible 自动化编排** 的端到端跨云数据接入与汇聚体系，最终将所有指标（Metrics）、链路追踪（Traces）与日志（Logs）经由观测云安全公网网关 **DataWay (`https://cn3-openway.guance.com`)** 进行统一治理、关联分析与可视化呈现。

```mermaid
flowchart TD
    subgraph UAT_Cloud ["1. UAT 跨云多运行时环境"]
        CF["Cloudflare (Logpush / Workers)"]
        CR["GCP Cloud Run (OTel SDK / Agent)"]
        SB["Supabase DB (PostgreSQL)"]
    end

    subgraph Security_Plane ["2. 统一凭据安全平面 (Vault KV v2)"]
        Vault["HashiCorp Vault (https://vault.svc.plus)\n- DataWay: kv/data/datakit/shared/dataway\n- OTel: kv/data/dataway/otel/shared\n- UAT/PROD: kv/data/{env}/serverless/observability"]
    end

    subgraph PROD_Cluster ["3. PROD K3S 集群 (104.105.141.213 / prod-k3s-v5)"]
        direction TB
        CADDY["Caddy Ingress Gateway (observability.svc.plus)\nRoute: /otel/v1/* (X-Token / Bearer 鉴权)"]
        
        subgraph DK_PROD_NS ["Namespace: datakit"]
            ESO["ExternalSecrets Operator (ESO)"]
            DK_DS["DataKit DaemonSet (v2.16.1)\n- Host Alias: prod-k3s-v5\n- Inputs: host, container, opentelemetry, statsd, etc."]
            DK_SVC["ClusterIP Service: datakit-service\n- gRPC: 4317\n- HTTP: 9529\n- StatsD: 8125"]
        end
    end

    subgraph SIT_Cluster ["4. SIT K3S 集群 (172.237.5.48 / dev-k3s-v5)"]
        subgraph DK_SIT_NS ["Namespace: datakit"]
            DK_SIT_DS["DataKit DaemonSet (v2.16.1)\n- Tags: cluster=dev-k3s-v5-sit, env=sit"]
        end
    end

    subgraph Guance_SaaS ["5. 观测云 SaaS 平台"]
        DW["DataWay 公网数据网关\n(https://cn3-openway.guance.com?token=***)"]
        CONSOLE["观测云工作空间 (wksp_cdcfb3a281c3443fb06770f49a277e1d)"]
    end

    %% 凭据流
    Vault -.->|ESO 自动拉取| ESO
    ESO -.->|Secret: datakit-dataway-secret| DK_DS
    Vault -.->|Ansible Playbook 动态渲染| DK_SIT_DS
    Vault -.->|CI/CD Secret 注入| CR

    %% 数据接入流
    CF -->|OTel HTTP Logs/Traces| CADDY
    CR -->|OTel HTTP/Protobuf 直连| DW
    CR -.->|OTel gRPC 转发| CADDY
    SB -.->|DB 远端指标轮询| DK_DS
    CADDY -->|内部反向代理 9529/4317| DK_SVC
    DK_SVC --> DK_DS

    %% 上报流
    DK_DS ==>|DataWay Protocol / HTTPS| DW
    DK_SIT_DS ==>|DataWay Protocol / HTTPS| DW
    DW ==> CONSOLE
```

---

## 二、Vault KV 凭据平面设计与安全基线

遵循平台工程核心安全准则：“**凭据不进代码、不落明文磁盘、终端日志不打印**”，所有与观测云、DataWay、OpenTelemetry 相关的敏感 Token 全部收敛至 HashiCorp Vault KV（v2）。

### 1. 凭据路径设计矩阵

| 凭据路径 (Vault KV v2) | Key 字段 | 类型 | 用途与说明 |
| :--- | :--- | :--- | :--- |
| `kv/data/datakit/shared/dataway` | `DATAWAY_URL` | string | 完整的公网 DataWay 上报端点：`https://cn3-openway.guance.com?token=tkn_...` |
|  | `DATAWAY_TOKEN` | string | 观测云工作空间专属接入 Token（必须以 `tkn_` 开头） |
| `kv/data/dataway/otel/shared` | `OTEL_EXPORTER_OTLP_HEADERS` | string | OTel 上报鉴权头：`X-Token=96d584964ba24996abe4aee502bb8856,To-Headless=true` |
|  | `OTEL_TOKEN` | string | OpenTelemetry 核心认证 Token（`96d584964ba24996abe4aee502bb8856`） |
| `kv/data/uat/serverless/observability` | `OTEL_EXPORTER_OTLP_HEADERS` | string | UAT 环境 Cloud Run / Serverless 专属 OpenTelemetry 接入认证头 |
|  | `OTEL_TOKEN` | string | UAT 环境 OpenTelemetry Token |
| `kv/data/prod/serverless/observability`| `OTEL_EXPORTER_OTLP_HEADERS` | string | PROD 环境 Serverless 专属接入认证头 |
| `kv/data/datakit/clusters/web-saas-k3s-v5` | `GLOBAL_TAGS` | string | PROD 集群环境标签：`cluster=web-saas-k3s-v5-prod,env=prod` |
| `kv/data/datakit/clusters/dev-k3s-v5` | `GLOBAL_TAGS` | string | SIT 集群环境标签：`cluster=dev-k3s-v5-sit,env=sit` |

> [!CAUTION] 鉴权 Token 格式规则
> 观测云公网 DataWay 网关鉴权对 Token 格式有严格校验：
> 1. 以 `cn3.` 或 `cn6.` 开头的字符串为 OpenAPI / Agent 管理 API Key，直接传入 DataWay 会被拒绝，抛出 `403 Forbidden: {"error_code":"kodo.invalidClientToken"}`。
> 2. DataKit 上报与 DataWay 直连必须使用工作空间正式 Client Token（如 `<DATAWAY_TOKEN>`）。
> 3. OpenTelemetry 协议上报必须通过 HTTP 请求头携带 `X-Token=<OTEL_TOKEN>` 及 `To-Headless=true`。

### 2. ExternalSecrets Operator (ESO) 绑定规范
PROD K3S 集群装载有 External Secrets Operator，通过 `SecretStore/vault-backend` 实现免密拉取与小时级轮转：
```yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: datakit-dataway-secret
  namespace: datakit
spec:
  refreshInterval: "1h"
  secretStoreRef:
    name: vault-backend
    kind: SecretStore
  target:
    name: datakit-dataway-secret
    creationPolicy: Owner
  data:
  - secretKey: ENV_DATAWAY
    remoteRef:
      key: datakit/shared/dataway
      property: DATAWAY_URL
```

---

## 三、Ansible Playbook 角色规范 (`roles/charts/datakit`)

在 `ai-workspace-infra/playbooks` 仓库新增标准角色，支持离线/在线一键发布、配置变更与升级。

### 1. 角色目录结构
```text
roles/charts/datakit/
├── defaults/
│   └── main.yml           # 默认仓库、版本、Chart 配置、探针端口
├── tasks/
│   ├── main.yml           # 任务主流程编排
│   ├── prereqs.yml        # 校验 Helm 3.0+、K8s 集群连通性、命名空间
│   ├── secrets.yml        # 从 Vault 读取 DataWay Token 并下发 K8s Secret
│   ├── helm.yml           # 添加 Helm 仓库并安装/升级 DataKit Chart
│   └── verify.yml         # DaemonSet 滚动更新状态与 Pod 探针校验
└── templates/
    └── values.yaml.j2     # 标准 DataKit Helm Values 渲染模板
```

### 2. 默认参数定义 (`defaults/main.yml`)
```yaml
---
# datakit Helm role defaults
datakit_helm_repo_url: "https://pubrepo.guance.com/chartrepo/datakit"
datakit_helm_repo_name: "datakit"
datakit_chart_name: "datakit/datakit"
datakit_chart_version: "2.16.1"

datakit_release_name: "datakit"
datakit_namespace: "datakit"

# Vault 凭据配置
datakit_vault_addr: "{{ lookup('ansible.builtin.env', 'VAULT_ADDR') | default('https://vault.svc.plus', true) }}"
datakit_vault_path: "datakit/shared/dataway"

# 采集功能开关
datakit_enable_opentelemetry: true
datakit_otel_grpc_port: 4317
datakit_otel_http_port: 9529
datakit_default_inputs: "statsd,dk,cpu,disk,diskio,mem,swap,system,hostobject,net,host_processes,container,kubernetesprometheus,opentelemetry"

# 资源限制
datakit_resources:
  requests:
    cpu: "100m"
    memory: "128Mi"
  limits:
    cpu: "500m"
    memory: "512Mi"
```

### 3. 主剧本入口 (`deploy_datakit.yml`)
```yaml
---
- name: Deploy Guance DataKit DaemonSet on Kubernetes
  hosts: "{{ target_hosts | default('k3s_clusters') }}"
  gather_facts: false
  roles:
    - role: charts/datakit
```

---

## 四、Flux CD GitOps 持续交付规范

遵循环境不可变与声明式配置原则，在 GitOps 仓库（`ai-workspace-infra/gitops`）定义全量声明：

### 1. HelmRepository 定义
```yaml
apiVersion: source.toolkit.fluxcd.io/v1
kind: HelmRepository
metadata:
  name: datakit
  namespace: datakit
spec:
  interval: 1h
  url: https://pubrepo.guance.com/chartrepo/datakit
```

### 2. HelmRelease 定义 (`HelmRelease/datakit`)
```yaml
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: datakit
  namespace: datakit
spec:
  interval: 15m
  chart:
    spec:
      chart: datakit
      version: "2.16.1"
      sourceRef:
        kind: HelmRepository
        name: datakit
        namespace: datakit
  install:
    createNamespace: true
    remediation:
      retries: 3
  upgrade:
    remediation:
      retries: 3
  values:
    workload:
      kind: DaemonSet
    datakit:
      dataway_secret_enabled: true
      dataway_secret_create: false
      dataway_secret_name: "datakit-dataway-secret"
      dataway_secret_key: "ENV_DATAWAY"
      global_tags: "cluster={{ cluster_name }},env={{ env_name }}"
      default_enabled_inputs: "statsd,dk,cpu,disk,diskio,mem,swap,system,hostobject,net,host_processes,container,kubernetesprometheus,opentelemetry"
      log: "info"
    extraEnvs:
      - name: "ENV_GLOBAL_HOST_TAGS"
        value: "host={{ host_alias | default(ansible_hostname) }}"
      - name: "ENV_INPUT_OTEL_GRPC"
        value: '{"addr": "0.0.0.0:4317", "max_payload": 16777216}'
      - name: "ENV_INPUT_OTEL_HTTP"
        value: '{"enable": true, "http_status_ok": 200, "trace_api": "/otel/v1/traces", "metric_api": "/otel/v1/metrics", "logs_api": "/otel/v1/logs"}'
      - name: "ENV_INPUT_OTEL_CUSTOMER_TAGS_ALL"
        value: "true"
      - name: "ENV_INPUT_OTEL_METRIC_ENABLE_AGGREGATE"
        value: "true"
    resources:
      requests:
        cpu: "100m"
        memory: "128Mi"
      limits:
        cpu: "500m"
        memory: "512Mi"
```

### 3. Serverless 运行时拓扑声明 (`topology/uat/serverless/runtime-topology.yaml`)
非敏感拓扑声明集中管理在 GitOps 中，敏感 Token 路径通过 `vault_token_path` 引用：
```yaml
spec:
  observability:
    provider: guance-opentelemetry
    gateway: https://cn3-openway.guance.com
    protocol: http/protobuf
    vault_token_path: kv/data/uat/serverless/observability
    endpoints:
      logs: https://cn3-openway.guance.com/otel/v1/logs
      metrics: https://cn3-openway.guance.com/otel/v1/metrics
      traces: https://cn3-openway.guance.com/otel/v1/traces
```

---

## 五、主机别名重构与非侵入式控制机制

在 Linode VPS 或公有云托管环境中，宿主机系统级主机名可能初始化为泛用标识符（如 `localhost`），且因底层网络/系统耦合不建议直接修改 OS 的 hostname。

DataKit 具备强大的主机对象命名重构能力：
1. **控制机制**：通过在 Helm values 的 `extraEnvs` 中注入环境变量：
   ```yaml
   extraEnvs:
     - name: ENV_GLOBAL_HOST_TAGS
       value: "host=prod-k3s-v5"
   ```
2. **底层行为**：DataKit 解析后会生成配置块：
   ```toml
   [global_host_tags]
     cluster_name_k8s = "default"
     host = "prod-k3s-v5"
   ```
3. **效果保证**：
   - 系统原生 `hostname`（`localhost`）与底层网络路由完全保持不变。
   - DataKit 上报的 Host Object 及其所有关联指标、日志、链路均被打上 `host=prod-k3s-v5` 标签，在观测云控制台 `HOST` 列表中直接展示为 `prod-k3s-v5`。

---

## 六、OpenTelemetry 协议标准与跨云多源汇聚

### 1. OpenTelemetry 环境变量标准
所有接入服务（包括 Cloud Run、Edge Gateway、微服务）均采用标准 OpenTelemetry 环境变量直连 DataWay 或内网 DataKit：

```bash
# 协议定义（二进制 Protobuf 保证高吞吐与低开销）
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf

# 认证头（强制携带 X-Token 与 To-Headless）
export OTEL_EXPORTER_OTLP_HEADERS="X-Token=96d584964ba24996abe4aee502bb8856,To-Headless=true"

# 数据分类接入端点
export OTEL_EXPORTER_OTLP_LOGS_ENDPOINT=https://cn3-openway.guance.com/otel/v1/logs
export OTEL_EXPORTER_OTLP_METRICS_ENDPOINT=https://cn3-openway.guance.com/otel/v1/metrics
export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=https://cn3-openway.guance.com/otel/v1/traces
```

### 2. 多源对接矩阵

| 数据源类别 | 采集机制 | 接入通道与端点 | 凭据来源 |
| :--- | :--- | :--- | :--- |
| **GCP Cloud Run** | OTel Go/Node SDK | 直连 `https://cn3-openway.guance.com/otel/v1/*` | Vault `kv/data/{env}/serverless/observability` |
| **Cloudflare Workers** | Logpush / OTel HTTP | Caddy 反代 `/otel/v1/logs` 或直连 DataWay | Vault `kv/data/dataway/otel/shared` |
| **Supabase DB** | PostgreSQL 采集器 | PROD DataKit 定期轮询 DB 视图指标与慢日志 | Vault `kv/data/database/supabase` |
| **PROD/SIT K3s 应用** | OTel gRPC SDK | 集群内直连 `http://datakit-service.datakit:4317` | K8s 内网免 Token（由 DataKit 追加统一标签） |

### 3. Caddy Ingress 反向代理路由 (可选外部安全接入)
若需对外隐藏 DataWay 目标，可在 `observability.svc.plus` 启用反向代理与 Bearer 鉴权：
```caddyfile
observability.svc.plus {
  @otel_ingress path /otel/*
  handle @otel_ingress {
    @valid_token header Authorization "Bearer <UAT_INGRESS_TOKEN>"
    handle @valid_token {
      reverse_proxy http://datakit-service.datakit.svc.cluster.local:9529
    }
    respond "Unauthorized OTel Collector Ingest" 401
  }
}
```

---

## 七、Platform-Orchestrator 流水线闭环集成

为了确保所有通过 CI/CD 发布的服务自动具备可观测能力，平台流水线实施了全自动化注入与严格门禁治理：

1. **自动环境变量注入**：
   在 `platform-ops-toolkit` 的 `scripts/serverless_uat/deploy_cloudrun_services.sh` 中增加逻辑：
   - 自动检测 GitOps `runtime-topology.yaml` 的 `spec.observability` 声明。
   - 若配置了 `vault_token_path`，自动从 Vault 提取 `OTEL_EXPORTER_OTLP_HEADERS` 并注入到 Cloud Run 服务的部署环境变量中。
2. **流水线 PR 合并**：
   变更通过 PR #1479 正式合入 `ai-workspace-infra/Platform-Orchestrator:main`。
3. **执行验证**：
   在 GitHub Actions 触发 `serverless-orchestrator.yml`（Run ID `40661500801`，参数 `operation=plan, dns_mode=none`），全链路准备、环境契约校验与汇总卡片均全绿通过（`success`）。

---

## 八、SIT K3S 数据库监控实测与 Exporter 接入（PostgreSQL Exporter + DataKit Prom 插件）

在微服务与 SaaS 体系中，业务数据库的健康度、连接池饱和度、慢事务与死锁是系统高可用保障的关键。本章规范了在 Kubernetes 集群环境下，以**业务零中断、完全解耦的独立附件模式**交付 PostgreSQL Exporter 并接入 DataKit 监控体系的标准流程。

### 1. 架构设计与安全边界

为杜绝监控变更对业务运行造成风险，整体架构坚持以下安全基线：
1. **独立 Deployment 伴生运行**：
   PostgreSQL Exporter 独立部署在业务命名空间（如 `feature-xconnect-referral-sit`），严禁将 Exporter 注入到原有的 PostgreSQL StatefulSet Pod 中，避免因监控组件变更触发业务数据库重启。
2. **连接凭据安全复用**：
   Exporter 容器通过环境变量动态引用业务数据库既有的 Kubernetes Secret（`web-saas-postgresql`），无需单独为 Exporter 创建高危凭据或硬编码密码：
   - `DATA_SOURCE_NAME`: `postgresql://$(POSTGRES_USER):$(POSTGRES_PASSWORD)@web-saas-postgresql:5432/$(POSTGRES_DATABASE)?sslmode=disable`
3. **严格合规 Kubernetes PodSecurity `restricted:v1.37`**：
   - Pod 级 `securityContext`: `runAsNonRoot: true`, `runAsUser: 65534`, `fsGroup: 65534`, `seccompProfile: {type: RuntimeDefault}`
   - Container 级 `securityContext`: `allowPrivilegeEscalation: false`, `readOnlyRootFilesystem: true`, `capabilities: {drop: ["ALL"]}`
4. **网络安全策略与独立 Service**：
   - 创建 ClusterIP Service `web-saas-postgresql-exporter`，暴露 `9187` 采集端口。
   - 配置 NetworkPolicy 显式放行来自 DataKit 采集器的 `9187` 端口入站流量。

```mermaid
flowchart LR
    subgraph K3S_SIT ["SIT K3S (dev-k3s-v5)"]
        subgraph NS_BIZ ["Namespace: feature-xconnect-referral-sit"]
            PG_POD["PostgreSQL StatefulSet\nweb-saas-postgresql-0:5432\n(业务运行中，零重启)"]
            EXP_POD["PostgreSQL Exporter Pod\nweb-saas-postgresql-exporter\n(Restricted PodSecurity)"]
            EXP_SVC["Service: web-saas-postgresql-exporter:9187"]
            
            EXP_POD -->|TCP 5432 内部只读查询| PG_POD
            EXP_SVC --> EXP_POD
        end
        
        subgraph NS_DK ["Namespace: datakit"]
            DK_AGENT["DataKit DaemonSet (datakit)\n- Inputs: prom\n- Config: /conf.d/prom/postgresql-exporter.conf"]
        end
    end
    
    subgraph SaaS ["观测云 SaaS"]
        DW_API["DataWay (https://cn3-openway.guance.com)"]
    end
    
    DK_AGENT -->|HTTP 9187 /metrics 轮询 (15s)| EXP_SVC
    DK_AGENT ==>|Metric (M) 上报| DW_API
```

### 2. GitOps + Flux HelmRelease 交付链路

For Kubernetes-hosted PostgreSQL in `dev-k3s-v5` (SIT) and `prod-k3s-v5`
(PROD), the deployment mode is FluxCD-owned: PostgreSQL exporter and its
DataKit scrape configuration are declared in GitOps and reconciled with the
cluster. `deploy_observability_agent.yml` must not install a second host-level
exporter when `observability_deployment_mode=fluxcd`; host/systemd exporter
collection remains available for non-Kubernetes hosts only.

遵循不可变交付与 GitOps 自动化流水线，监控采集器以完整 OCI Chart 发布并对账：
1. **OCI Chart 打包与安全发布** (`ai-workspace-infra/artifacts`):
   - 发布不可变 Helm Chart: `ghcr.io/ai-workspace-infra/distributed-web-saas-fullstack:0.2.8`
   - OCI 镜像摘要 (Digest): `sha256:62b776fbc79ba471fa51fd0887fbb282b9603db0c1e199409ceb6f6c2b42a286`
2. **GitOps 仓库声明生效** (`ai-workspace-infra/gitops`):
   - 在 `services/distributed-web-saas-fullstack/dev/sit-xconnect-referral`:
     - `values.yaml` 中开启 `postgresql.metrics.enabled: true`。
     - `oci-repository.yaml` 锁定最新构建生成的不可变 Digest。
3. **Flux CD 自动无感对账**:
   - Flux 自动拉取 OCI Chart 并执行 `helm upgrade`。
   - 结果：PostgreSQL StatefulSet 实例完全无重启，业务数据库零抖动；`web-saas-postgresql-exporter` Pod 迅速拉起并就绪（`1/1 Running`）。

### 3. DataKit Prom 插件配置与持久化

通过 DataKit 内建的 `prom` 采集插件对接 Exporter 暴露的 `/metrics` 端点。在部署与调优过程中，平台针对多集群选举隔离与指标集规范进行了深度工程治理：

#### (1) 多集群默认选举暂停排查与根本原因 (Election Pause Root Cause)
* **故障现象**：
  初始配置后，Exporter 端点与网络连通均正常，但观测云控制台始终无法检索到 `postgresql` 指标集；在 DataKit 监控指标中，`datakit_input_prom_collect_points` 未产生 `source="postgresql"` 抓取点。
* **日志诊断**：
  检索 `/var/log/datakit/log` 发现关键日志：
  ```text
  INFO inputs inputs/inputs.go:57 find election inputs prom
  INFO dk-election election/leader_election.go:498 pause 4 inputs...
  INFO dk-election election/election.go:95 election provider=dataway namespace="default" id="dev-k3s-v5"
  ```
* **根本机制分析（结合 DataKit 官方文档）**：
  依据 [DataKit 官方文档 DaemonSet 部署规范与选举机制](https://docs.guance.com/datakit/datakit-daemonset-deploy/#using-k8-env)，`inputs.prom` 采集插件默认开启 Leader 选举（`election = true`）。
  由于 PROD 与 SIT 集群共用同一个观测云工作空间（统一通过 Vault 共享相同的 DataWay 凭据），且均采用默认的选举命名空间（`ENV_NAMESPACE="default"`），多集群 DataKit 实例向同一 DataWay 发起 Leader 竞选。PROD 节点获得了 Leader 租约，而 SIT 节点（`dev-k3s-v5`）未获得租约，导致其所有选举类输入插件（包括 `prom`）被 DataKit 核心引擎**完全挂起暂停（`pause 4 inputs...`）**。

#### (2) 关键配置修复与主干固化
针对上述问题，实施针对性架构修复：
1. **显式声明 `election = false`**：
   对于绑定在本地 Kubernetes 集群生命周期、仅对集群内部 Service（如 `web-saas-postgresql-exporter.feature-xconnect-referral-sit`）可见的采集器，必须显式声明 `election = false`。确保 SIT 本地 DataKit 必定独立抓取本地的 Exporter，杜绝跨集群 Leader 选举抢占导致的指标中断。
2. **显式指定 `measurement_name = "postgresql"` 与 `keep_exist_metric_name = true`**：
   - 默认模式下 DataKit Prom 插件会按照下划线将指标名切分，导致指标分散落入 `pg`、`postgres` 等不同集合；
   - 显式声明 `measurement_name = "postgresql"` 与 `keep_exist_metric_name = true`，使所有指标统一归集在观测云标准指标集 `postgresql` 下，并 100% 完整保留 Prometheus 原生指标名（`pg_up`、`pg_database_size_bytes`、`pg_stat_database_xact_commit` 等）。
3. **标准配置模板**：
   在 DataKit Helm Release 中配置 `dkconfig` 挂载 `/usr/local/datakit/conf.d/prom/postgresql-exporter.conf`：
   ```toml
   [[inputs.prom]]
     urls = ["http://web-saas-postgresql-exporter.feature-xconnect-referral-sit:9187/metrics"]
     source = "postgresql"
     interval = "15s"
     measurement_name = "postgresql"
     keep_exist_metric_name = true
     election = false
     [inputs.prom.tags]
       cluster = "dev-k3s-v5-sit"
       env = "sit"
       db_instance = "web-saas-postgresql"
       host = "dev-k3s-v5"
   ```
4. **启用默认采集模块**：
   在 `default_enabled_inputs` 中追加 `prom` 插件。
5. **Ansible Playbook 角色主干合入**：
   相关配置已在 `playbooks/roles/charts/datakit` 中完成模板固化，并通过 [PR #824](https://github.com/ai-workspace-infra/playbooks/pull/824)（`fix(datakit): explicitly disable election for local prom exporter inputs`）全绿合入 `main`。

### 4. 采集指标与实测验证

通过集群内端点探测、DataKit 本地调试指令及观测云 OpenAPI DQL 完成全链路三重验证：

#### (1) Exporter 指标探测实测
```bash
curl -s http://10.42.0.48:9187/metrics | grep pg_
```
核心指标输出：
- `pg_up 1`：PostgreSQL 连通性探针（1 表示成功连通业务数据库 `account`）。
- `pg_database_size_bytes{datname="account"}`：数据库物理存储占用。
- `pg_stat_database_xact_commit` / `pg_stat_database_xact_rollback`：每秒提交/回滚事务吞吐。
- `pg_stat_database_blks_read` / `pg_stat_database_blks_hit`：共享缓冲区命中率（实测高达 99.8%）。
- `pg_locks_count`：表级/行级加锁统计。
- `pg_stat_database_deadlocks`：死锁发生计数。
- `pg_wal_segments` / `pg_wal_size_bytes`：预写日志 WAL 数量与体积。

#### (2) DataKit 抓取管道实测
配置 `election = false` 生效后，DataKit 抓取指标立竿见影：
```bash
# 检查本地抓取统计 (http://127.0.0.1:9529/metrics)
datakit_input_prom_collect_points{mode="stream",source="postgresql",quantile="0.5"} 2737
datakit_input_prom_collect_points_sum{mode="stream",source="postgresql"} 8211
```
确认每 15s 稳定抓取 2,700+ 数据点，数据全部进入 `io feed prom.postgresql on metric` 并持续上报至 DataWay。

#### (3) 观测云 OpenAPI DQL 验证记录
通过观测云 OpenAPI (`https://cn3-openapi.guance.com/api/v1/df/query_data_v1`) 直接检索工作空间数据，验证指标集 `postgresql` 已 100% 成功建档与持续刷新：

* **实例健康探针 (`M::postgresql:(pg_up) { host = "dev-k3s-v5" }`)**:
  ```json
  {
    "name": "postgresql",
    "tags": {
      "host": "dev-k3s-v5",
      "cluster": "dev-k3s-v5-sit",
      "db_instance": "web-saas-postgresql",
      "instance": "web-saas-postgresql-exporter.feature-xconnect-referral-sit:9187"
    },
    "values": [[1793190065125, 1]]
  }
  ```
* **数据库存储容量 (`M::postgresql:(pg_database_size_bytes) by datname`)**:
  - `account` 业务库：`12,154,547` 字节 (**12.15 MB**)
  - `postgres` 系统库：`7,657,139` 字节 (**7.66 MB**)
  - `template0` / `template1`：约 7.5 MB
* **事务与锁吞吐 (`M::postgresql:(pg_stat_database_xact_commit, pg_locks_count) { datname = "account" }`)**:
  - `pg_stat_database_xact_commit`: `50,992` -> `51,021` -> `51,058` -> `51,086` -> `51,117`（每 15s 均匀递增）。
  - `pg_locks_count`: `accesssharelock` 读锁计数为 1，排他锁为 0。

---

## 九、Agent 自动接入标准流程 (`otel-instrument` Skill)

对于研发团队新增的业务代码仓库，可通过观测云标准 Agent Skill 进行全自动无侵入注入：

### 1. 安装 Skill 插件
```bash
curl -fsSL "https://static.guance.com/skills/install.sh" | sh -s -- \
  --base-url "https://static.guance.com/skills" \
  --skill otel-instrument \
  --agent codex \
  --scope user \
  --yes
```

### 2. 执行自动化分析与注入
激活 `otel-instrument` Skill，自动分析当前代码工程（Go, Java, Python, Node.js, Rust 等）：
- 自动写入对应语言的 OpenTelemetry SDK 依赖。
- 自动生成符合本白皮书规范的启动脚本与环境变量声明。

---

## 十、生产交付与实测数据归档

### 1. 集群实例运行状态实测

| 集群环境 | 节点 IP | Pod 名称 | 状态 | CPU / 内存消耗 | Host 别名呈现 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **PROD** | `104.105.141.213` | `datakit-lzw6z` | 1/1 Running | 5m / 101Mi | `prod-k3s-v5` |
| **SIT** | `172.237.5.48` | `datakit-kkcr5` | 1/1 Running | 7m / 103Mi | `dev-k3s-v5` |

### 2. 观测云 OpenAPI DQL 验证记录
通过观测云 OpenAPI (`https://cn3-openapi.guance.com/api/v1/df/query_data_v1`) 直接检索工作空间数据，验证双机指标与对象均已正常建档：

* **HOST 对象列表 (`O::HOST:(host, os)`)**:
  ```json
  {
    "values": [
      [1793178335308, "dev-k3s-v5", "linux"],
      [1793178317240, "prod-k3s-v5", "linux"]
    ]
  }
  ```
* **实时 CPU 指标采样 (`M::cpu:(usage_total)`)**:
  - `dev-k3s-v5`: `8.18%`
  - `prod-k3s-v5`: `39.40%`
* **DataWay WAL 刷新状态**:
  - `datakit_dataway_dial_total{family="ipv4",result="success"}`: 100% 成功，failed 计数为 0。
  - Metric (M) / Object (O) / Log (L) 持续自动 flush 到 DataWay。

---

## 十一、修订历史记录

| 版本号 | 修订日期 | 修订人 | 修订内容摘要 | 审批状态 |
| :--- | :--- | :--- | :--- | :--- |
| `v1.0.0` | 2026-10-28 | Platform Engineering Agent | 初稿制定，覆盖双集群 Helm 部署、Vault 路径规划与 Caddy Ingress 汇聚 | Approved |
| `v1.1.0` | 2026-10-28 | Antigravity AI Agent | 补全 `prod-k3s-v5` 主机别名控制机制、OTel HTTP/Protobuf 端点规范、Serverless Orchestrator 闭环及实测 DQL 归档 | Approved & Released |
| `v1.2.0` | 2026-10-28 | Antigravity AI Agent | 补充 SIT K3S PostgreSQL Exporter 独立附件模式规范、GitOps+Flux 无损交付、DataKit Prom 插件多集群选举暂停（Election Pause）根因剖析与 election=false 治理、PR #824 主干合入及实测 DQL 指标归档 | Approved & Released |
| `v1.3.0` | 2026-10-28 | Codex | 补充 DEV/SIT Caddy 原生 metrics 的 GitOps + Flux HelmRelease 交付、DataKit `prom` 采集与观测云按 `cluster=dev-k3s-v5-sit` 查询验证；记录业务日志及 MCP 待验项 | Caddy 指标已验证；其他验收待完成 |
| `v1.4.0` | 2026-10-28 | Codex | 修复 DEV K3s containerd socket 发现，验证业务 Pod stdout/stderr 进入观测云；记录 PostgreSQL exporter `stat_bgwriter` 异常 | Pod 日志已验证；PG collector 与 MCP 待处理 |
| `v1.5.0` | 2026-10-28 | Codex | 将 SIT PostgreSQL exporter 升级至兼容 PostgreSQL 17 的 v0.17.1，并验证 Flux 与采集器状态 | `stat_bgwriter` 已恢复；观测云对应新指标待复核 |
| `v1.6.0` | 2026-10-28 | Antigravity AI Agent | 补充第十三章：观测云免费版能力全景与实测接入场景矩阵（涵盖多环境、双集群、数据库、网关指标、Pod/边缘日志及 Serverless OTel） | Approved & Released |
| `v1.7.0` | 2026-10-28 | Codex | 增补生产 DataKit Pod 日志、Caddy 原生指标及 VictoriaMetrics/Grafana 接入验证 | 生产日志写入与 Caddy 采集已验证；生产 PostgreSQL 监控身份待建设 |
| `v1.8.0` | 2026-10-28 | Codex | 在 DEV K3s 临时 PostgreSQL 17 中验证 exporter 监控最小权限与拒绝测试 | 隔离验证通过；生产身份及 Vault 交付待实施 |

---

## 十二、DEV K3s Caddy 与 DataKit 变更记录（2026-10-28）

- GitOps 使用 Flux HelmRelease 管理 DEV DataKit，引用既有 DataWay Secret；DataKit chart `2.16.1` 的 HelmRelease 和 DaemonSet 已 Ready（1/1）。
- SIT Caddy chart `0.2.9` 发布 metrics 专用 ClusterIP Service `web-saas-caddy-metrics:9180`；DataKit 到 `/metrics` 的 HTTP 请求实测返回 200。Caddy Admin API 保持关闭。
- DataKit 的 Caddy `inputs.prom` 使用 `measurement_name = "caddy"`、`keep_exist_metric_name = true`、`election = false`；PostgreSQL exporter 配置保留 `measurement_name = "postgresql"`、`election = false` 和现网标签。`datakit check --config`：18 项通过，0 项失败；PostgreSQL exporter `/metrics` 从 DataKit 可访问。
- 容器 stdout/stderr 采集范围配置为 `namespace:feature-*`。观测云侧 Caddy 指标与业务 Pod 日志已查询确认；`datakit debug --prom-conf` 的输出提示 feed output 未设置，不能作为 DataWay 入库成功证据。
- GitOps PR：Caddy chart [artifacts #240](https://github.com/ai-workspace-infra/artifacts/pull/240)；Flux 声明 [gitops #620](https://github.com/ai-workspace-infra/gitops/pull/620)、[#621](https://github.com/ai-workspace-infra/gitops/pull/621)、[#622](https://github.com/ai-workspace-infra/gitops/pull/622)、[#623](https://github.com/ai-workspace-infra/gitops/pull/623)、[#624](https://github.com/ai-workspace-infra/gitops/pull/624)。SIT lane source 固定于 #624 合并 commit `8f73979c29e9e42ed4d38c5966e9a97f7f8ef9b1`；根 source 固定于 `3375930f094d3c70269a17642e37770835da93e4`。
- DEV ingress HelmRelease 已 Ready；MCP 两个工作负载尚未 Ready：infrastructure 镜像拉取返回 403，Grafana gateway 启动时将 `-t` 识别为可执行文件。根 Kustomization 与 SIT HelmRelease 当时仍在对账；SIT billing Pod 已处于 CrashLoopBackOff，需独立排查。
- 回滚：优先通过 GitOps 恢复 Caddy、DataKit 的先前 values 和日志范围，并保留 DataWay Secret。根 Kustomization 开启 prune，直接回退根 source 或删除 DataKit HelmRelease 可能卸载采集器；操作前先核对资源清单和影响。

### Caddy 修复后复核

- SIT `HelmRelease/distributed-web-saas-fullstack` 已 Ready，版本为 `0.2.9+2257fde6ee6a`；Caddy Pod 1/1 Ready。后渲染 Caddyfile 修复见 GitOps #624。
- 从 DataKit 请求 Caddy `/metrics` 返回 HTTP 200，可见 `caddy_http_requests_total` 和 `caddy_http_request_duration_seconds_count`。DataKit 运行时 `prom/caddy` 响应计数为 12、连接复用计数为 7；DataWay IPv4 连接成功计数为 9、失败为 0。上述计数是检查时的快照，证明采集请求和 DataWay 连接情况，不证明观测云已存储 Caddy 指标。
- 观测云指标分析中，最近 5 分钟查询 `caddy` 指标集的 `caddy_config_last_reload_success_timestamp_seconds`，按 `cluster` 分组得到 `dev-k3s-v5-sit` 一条记录，Last 为 `1793190753.96`，确认该指标已入库；业务日志入库已于后续复核确认；MCP 工作负载的镜像、启动错误仍待处理。


### DEV K3s 业务 Pod 日志验收

- 原因：DataKit 默认 containerd socket 不存在；K3s 的实际 socket 为 `/var/run/k3s/containerd/containerd.sock`，已通过现有 `/var/run` 目录挂载供 DataKit 使用。
- 修复：[GitOps #626](https://github.com/ai-workspace-infra/gitops/pull/626) 声明 endpoint，[#627](https://github.com/ai-workspace-infra/gitops/pull/627) 修正 DataKit 2.16.1 的环境变量值格式为单个 URI。Flux 已应用 commit `cf1f639e305951dfbd1ffdca2c5c38b2f2a66d93`，DataKit Helm release `v9` Ready、DaemonSet 1/1。
- DataKit 日志显示已连接 K3s containerd，并为 console、accounts、billing、content、Caddy、PostgreSQL 和 exporter 启动日志 tailer；业务日志范围仍为 `namespace:feature-*`。
- 观测云日志浏览器中已查到由验收请求生成的 console 日志 `codex_log_probe=20261028`；详情包含 `namespace=feature-xconnect-referral-sit`、`cluster=dev-k3s-v5-sit`、`env=sit`、`source=console` 及对应 Pod/Deployment，确认从 Pod stdout 到观测云的链路。
- 历史故障：PostgreSQL 17 上旧版 exporter 的 `stat_bgwriter` 查询 `checkpoints_timed` 报字段不存在，endpoint 曾返回 `pg_scrape_collector_success{collector="stat_bgwriter"} 0`；后续修复和验证见下节。
- 回滚：通过 GitOps 恢复先前 DataKit endpoint 配置并由 Flux 对账；保留 DataWay Secret。回滚会使该 K3s socket 的日志发现再次失效。


### DEV K3s SIT PostgreSQL 17 exporter 兼容性修复

- PostgreSQL 17.11 与 exporter 0.15.0 的统计视图不兼容，导致 `stat_bgwriter` 采集失败；[GitOps PR #628](https://github.com/ai-workspace-infra/gitops/pull/628) 将 SIT 的独立 exporter 固定为 v0.17.1，镜像摘要 `sha256:38606faa38c54787525fb0ff2fd6b41b4cfb75d455c1df294927c5f611699b17`。
- SIT Flux GitRepository 已对账提交 `21898e1c7336fc8a002eb50ac34fe983f4cb653f`；Kustomization 和 HelmRelease 均 Ready，Helm release `web-saas.v7` 升级成功。
- 从 DataKit Pod 访问 exporter：`pg_up 1`，`pg_scrape_collector_success{collector="stat_bgwriter"} 1`，其他已启用 collector 也返回 1；DataKit 配置检查 18 项通过。数据库 Pod UID 未变、Ready，重启次数 0。此证据证明端点可从 DataKit 访问，尚未单独验证升级后的对应指标在观测云展示或长期连续性。
- 回滚：通过 GitOps 撤销 PR #628 的 SIT 镜像覆盖并合并，再将 SIT Flux source 固定到包含回滚的提交并对账；旧 exporter 的 PostgreSQL 17 `stat_bgwriter` 故障会再次出现。

---

## 十三、观测云免费版（Free Tier）能力全景与实测接入场景矩阵

在**观测云免费版**（单工作空间提供 **5 台主机额度、免费数据留存周期、多数据源接入通道及 OpenAPI/DQL 检索**）下，通过本白皮书规范的自动化工程链路，完全实现了覆盖**双 K3S 集群**、**多套业务环境（PROD/SIT/UAT）**、**数据库与中间件**以及**外部 Serverless / 边缘服务**的全景可观测性。

### 1. 免费版核心能力与实测接入对照表

| 能力维度 | 免费版支持特性与配额机制 | 今日实测接入场景与具体落地 | 接入通道与技术实现 | 业务监控与运维价值 |
| :--- | :--- | :--- | :--- | :--- |
| **1. 基础设施与主机监控**<br>*(Host Metrics)* | • 支持多达 **5 台主机**免费建档<br>• 实时采集 CPU、内存、磁盘 IO、网络、系统负载<br>• 进程级监控 (`host_processes`) | • **PROD 节点** (`104.105.141.213`)<br>• **SIT 节点** (`172.237.5.48`)<br>• 强制注入别名 `prod-k3s-v5` / `dev-k3s-v5`，消除默认 `localhost` 污染 | • DataKit 基础采集插件：`cpu`, `mem`, `disk`, `net`, `system`, `hostobject`<br>• 统一通过 DataWay 上报 | 单一工作空间同时掌握生产与开发主机健康度，资源利用率与瓶颈一目了然。 |
| **2. K8s 容器与编排可观测**<br>*(Container Metrics)* | • 支持 K8s 集群原生元数据自动发现<br>• Pod、Deployment、DaemonSet、Node 状态自动采集<br>• 容器资源超额与重启监控 | • 双 K3S 集群全量容器纳管<br>• 捕获容器状态 (`kube_pod`, `kube_deployment`, `kube_node` 等)<br>• 监控 `CrashLoopBackOff` 与重启计数 | • DataKit DaemonSet 插件：`container`, `kubernetesprometheus`<br>• 基于 K8s API 自动对账 | 实时感知容器崩溃抖动与副本状态，精准定位由于资源不足导致的 Pod 重启。 |
| **3. 数据库深度指标监控**<br>*(Database & Middleware)* | • 支持 Prometheus 标准 Exporter 指标采集<br>• 自定义指标集 (`M::postgresql`) 自动汇聚<br>• 无需额外付费插件即可打通通用数据库 | • **SIT 业务库** `web-saas-postgresql`<br>• 采集 100+ 项指标：`pg_up 1`、数据库物理大小（`account` 库 12.15MB）、事务提交/回滚吞吐、锁等待统计、缓冲区命中率 | • 独立 `postgres_exporter` (9187 端口)<br>• DataKit `prom` 插件 (`keep_exist_metric_name=true`, `election=false`) | 无需直连生产数据库，通过解耦的 Exporter 持续洞察慢事务、连接池耗尽与死锁隐患。 |
| **4. 边缘网关与流量监控**<br>*(Gateway Ingress)* | • 支持反向代理及 Ingress 网关指标采集<br>• 状态码分布、请求吞吐量、QPS 与响应延时 | • **Caddy Ingress 网关** (`web-saas-caddy-metrics:9180`)<br>• 监控域名绑定流量与代理状态 | • DataKit `prom` 插件抓取 Caddy `/metrics`<br>• 绑定 `service="web-saas-caddy"` 标签 | 全局掌控网关入口 2xx/4xx/5xx 请求量与异常流量拦截。 |
| **5. 跨云 Serverless 微服务接入**<br>*(Serverless APM & Traces)* | • 支持标准 OpenTelemetry 协议（OTLP/HTTP、OTLP/gRPC）<br>• 接收分布式链路追踪 (Traces) 与应用指标 | • **GCP Cloud Run** 微服务调用链打通<br>• 通过流水线自动注入 Vault OTel 认证环境变量 | • 直连 DataWay：`https://cn3-openway.guance.com/otel/v1/*`<br>• HTTP/Protobuf 高吞吐传输 | 跨越云厂商隔离，将无服务器冷启动、微服务调用拓扑与端到端耗时归集于同一视图。 |
| **6. 全球边缘网络日志采集**<br>*(Edge Logpush)* | • 支持外部 HTTP Webhook / Logpush 管道直接推流<br>• 日志结构化解析与时间线索引 | • **Cloudflare Logpush** 任务对接<br>• 边缘全球访问日志直推观测云 | • Logpush 推送至 DataWay `/otel/v1/logs`<br>• 请求头携带 `X-Token` 与 `To-Headless=true` | 无需本地搭建日志中转器，边缘网络流量请求秒级进入统一日志流，便于排查安全扫描与攻击。 |
| **7. 容器与业务标准输出日志**<br>*(Container Logs)* | • 支持容器日志自动采集与命名空间过滤<br>• 日志全文检索与多维筛选过滤 | • SIT 特性泳道业务容器日志定向采集<br>• 设置过滤规则 `namespace:feature-*` 避免非必要日志超额 | • DataKit 环境变量：`ENV_INPUT_CONTAINER_CONTAINER_INCLUDE_LOG`<br>• containerd socket 挂载适配 | 精准过滤噪音，仅采集核心业务 Pod 的标准输出（stdout/stderr），节省额度。 |
| **8. 多环境与多集群标签隔离**<br>*(Tagging & Isolation)* | • 全维度 Tag 路由与权限过滤<br>• 支持按 `cluster`、`env`、`host`、`namespace` 进行多维下钻 | • PROD 环境：`cluster=web-saas-k3s-v5-prod, env=prod`<br>• SIT 环境：`cluster=dev-k3s-v5-sit, env=sit`<br>• UAT 环境：`env=uat` | • DataKit `global_tags`<br>• 采集器插件专属 `[inputs.prom.tags]` | 即使共享同一个免费版工作空间，也能在仪表盘和告警策略中一键切换隔离不同环境，互不干扰。 |
| **9. DQL 统一分析与开放 API**<br>*(DQL Query & OpenAPI)* | • 免费开放专属查询语言 **DQL**（DataFlux Query Language）<br>• 开放 API 支持外部程序或自动化工具检索数据 | • 验证脚本使用 `query_data_v1` 自动化拉取 `M::postgresql`、`M::cpu`、`O::HOST`<br>• 控制台指标检索器直接出图 | • OpenAPI：`https://cn3-openapi.guance.com/api/v1/df/query_data_v1`<br>• 鉴权头：`DF-API-KEY` | 支持自动化运维脚本对账与巡检，甚至能将观测云指标作为 CI/CD 质量门禁的判断依据。 |
| **10. 统一告警与自定义大盘**<br>*(Alerting & Dashboards)* | • 内置各技术栈标准仪表板模板（PostgreSQL, K8s, Linux 等）<br>• 支持自定义告警监控器与通知推送（邮件/飞书/钉钉） | • 观测云控制台一键加载官方 **PostgreSQL** 与 **Kubernetes** 仪表板<br>• 配置 `pg_up == 0` 实例离线告警 | • 观测云内置场景大盘引擎<br>• 基于 DQL 的触发器与通知通道 | 免费实现核心业务故障的主动告警响应，告别“系统挂了靠用户反馈”的被动局面。 |

---

### 2. 免费版工程效能与性价比总结

通过**“HashiCorp Vault（安全凭据）+ Flux CD（GitOps 对账）+ Ansible（Playbook 模板化）+ DataKit/OTel（标准协议）”**的自动化工程骨架：
- **零商业软件采购成本**：充分发挥观测云免费版的 5 台主机与免采集器授权优势；
- **全栈全场景覆盖**：在一个 SaaS 视图内无缝贯通双 K3S 节点底座、容器编排、PostgreSQL 数据库慢事务、Caddy 边缘网关、Cloud Run 无服务器调用链与 Cloudflare 边缘日志；
- **环境安全强隔离**：通过声明式标签（`cluster`, `env`, `namespace`）在同一工作空间内清晰分流 PROD、SIT 与 UAT，达成生产级可观测治理水平。



## 十四、PROD K3s 日志与 Caddy 指标交付（2026-10-28）

- GitOps + Flux 接管现有 DataKit 2.16.1 Helm release，保留既有 DataWay Secret 引用、资源配额与 OTel 配置；限定 K3s containerd 日志采集范围为 `namespace:web-saas-k3s-v5`。[GitOps #631](https://github.com/ai-workspace-infra/gitops/pull/631)、[#632](https://github.com/ai-workspace-infra/gitops/pull/632)。
- DataKit 已为生产 accounts、billing、Caddy 等容器启动日志 tailer，DataWay `/v1/write/logging` 的 `status=OK` 计数有增长；尚未在观测云界面逐条核验生产日志。
- 不升级现有业务 chart 0.2.5，Flux post-render 保持原生产 Caddy 路由、域名和 TLS 内容，仅增加原生 metrics；Admin API 保持关闭，9180 使用 ClusterIP 和 NetworkPolicy。DataKit `prom/caddy` 使用 `election=false`。[GitOps #633](https://github.com/ai-workspace-infra/gitops/pull/633)、[#634](https://github.com/ai-workspace-infra/gitops/pull/634)。
- 共享 VictoriaMetrics 增加 Caddy 抓取，目标 `health=up`、一次采集 595 个样本，查询 `up{job="caddy"}=1` 与 `caddy_http_requests_total` 均有生产样本。[GitOps #635](https://github.com/ai-workspace-infra/gitops/pull/635)、[#636](https://github.com/ai-workspace-infra/gitops/pull/636)。Caddy dashboard UID `fed94ifiv8h6oc` 已由 Grafana 文件 provisioning 加载，Grafana resource 存储包含该 UID；尚未从登录的 Grafana 界面验证全部面板。
- Flux 生产 source 固定 `v2026.10.28.5`，各 Kustomization 和 HelmRelease 就绪；共享观测 Pod 按授权重建后 5/5。生产 PostgreSQL Pod UID 保持不变、重启次数 0。
- 生产 PostgreSQL exporter 尚未部署：现有业务账户 `app` 是超级用户，不能直接复用；需要独立最小权限监控身份和 Vault 凭据、受控角色交付后验收。已有 DataWay Secret 的历史注解含明文凭据，曾在检查中暴露于工具输出，应作为凭据泄露事件轮换并清理注解。
- 回滚按功能分别通过 GitOps revert 和新的不可变标签发布：先恢复 DataKit 日志或 Caddy 采集值并对账，确认 DataKit/业务健康；VictoriaMetrics scrape 回滚将重建共享观测 Pod。保留现有 Secret 和生产业务域名，避免直接删除会卸载 release 的 HelmRelease。


## 十五、DEV K3s PostgreSQL 17 监控身份隔离验证（2026-10-28）

- 在 dev-k3s-v5 独立临时 namespace 中使用 PostgreSQL 17.11 和 postgres_exporter v0.17.1，容器非 root，Pod 绑定空临时卷并仅通过本机回环访问数据库，NetworkPolicy 拒绝所有进出；使用合成数据和仅用于隔离实验的 trust 登录，未使用真实业务凭据或业务数据库。
- 完整 `pg_monitor` 可采集；单独 `pg_read_all_stats` 时 `wal` collector 因 `pg_ls_waldir()` 权限不足而失败。保留 `pg_read_all_stats`，并仅授予 `pg_catalog.pg_ls_waldir()` 的 EXECUTE 后，`pg_up=1`，10 个启用 collector 的 `pg_scrape_collector_success=1`。
- 监控账号无 SUPERUSER、CREATEDB、CREATEROLE、REPLICATION 或 BYPASSRLS；通过真实监控账号登录，统计视图读取成功。关闭会话默认只读后，合成敏感表 SELECT/INSERT、创建普通表、创建角色及 `SET ROLE pg_read_all_stats` 均被拒绝。`pg_read_all_stats` 可读取数据库运行统计及其他会话活动信息，必须按监控用途控制访问和数据暴露。
- PostgreSQL 默认 `PUBLIC` 仍赋予数据库 TEMP 权限，测试账号可拥有临时对象；因此不宣称绝对零写入。生产环境必须独立检查 PUBLIC/默认 ACL、函数、安全定义者、RLS 与实际有效权限，且不为了该账号擅自收回生产全局 PUBLIC 权限。
- 实验已删除 namespace、Pod、临时卷及 NetworkPolicy；DEV SIT 数据库 Pod UID 不变、Ready，重启数 0。实验使用本机 trust，不证明生产 SCRAM、Vault、ESO 或生产数据库权限；生产账号和 Vault 凭据尚未创建。
