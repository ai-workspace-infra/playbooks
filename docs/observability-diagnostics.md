# XConnect diagnostics on the existing monitoring stack

Use `deploy_observability_diagnostics.yml` with `observability_accounts_hosts`
and the existing `observability_hosts` inventory. It calls the existing
Accounts runtime role and observability server role, not a second server or
collector. Accounts must already run the identity resolver API. No application
image is rebuilt or upgraded; the Accounts image digest must match its running
Compose declaration. Only Accounts and Grafana may be recreated.

The existing monitoring record `kv/data/CICD/observability` remains the source
for `user` and `password`. The focused Accounts repair may provision a missing
`IDENTITY_LOOKUP_TOKEN` field using a compare-and-set write that preserves all
existing fields. Agents still receive only the existing ingest user/password;
the identity token is not copied to them. The legacy diagnostics Vault path can
still be selected explicitly with `observability_xconnect_diagnostics_vault_path`.
Never reuse an ingest password or `INTERNAL_SERVICE_TOKEN` for identity lookup.

Accounts already consumes `/etc/xcontrol/web-saas/accounts-smtp.env` as an
Accounts-only runtime environment. The repair updates just the resolver key in
that file, retaining SMTP. SMTP rotation preserves this separately managed key.
The historical filename is retained to avoid another GitOps mount/config source.
The credential must not be inserted into the database/shared services env file.

The server verifies the resolver accepts its scoped token (an empty POST must
produce authenticated validation status 400, not 401/503) before installing the
pinned/checksummed plugin release. A minimal Compose override preserves the
running Grafana image ID and existing plugin allowlist; only this one plugin is
added. Existing databases, collectors, metrics/log sinks and Caddy are unchanged.
Full server installs and focused repairs call the same plugin lifecycle tasks.
The shared plugin mount root belongs to Grafana UID/GID 472, including when this
app is disabled, so existing plugins can still be installed at container startup.
The named observability host opts in through host vars so a future full server
deployment retains the feature.

Acceptance requires plugin registration, enabled provisioning, authenticated
resource calls, missing-identity handling, upstream failure handling and raw
identity exclusion from URLs/responses. Anonymous resource calls must fail.
Page loading alone does not prove any real connection Trace exists. An empty
Trace result is not zero latency or proof that a user had no connections.

For rollback, restore the protected Accounts env backup and recreate only its
existing pinned service. Restore the prior Grafana provisioning/Compose settings
from the deployment backup and recreate only Grafana using its recorded image.
Removing the override alone does not disable an already provisioned app. Do not
rotate shared ingest credentials as rollback.

---

# Observability 数据闭环只读验收探针与发布/回滚方案 (OBS-05)

关联 Issue：父 Issue [`ai-workspace-infra/gitops#557`](https://github.com/ai-workspace-infra/gitops/issues/557) | 子 Issue [`ai-workspace-infra/playbooks#767`](https://github.com/ai-workspace-infra/playbooks/issues/767)

## 1. 可重用只读验收探针与门禁校验器

Playbooks 提供独立、无副作用的控制器端只读验收探针：

- **独立脚本入口**：`scripts/verify_observability_closure.py`
- **Ansible Playbook / Role 入口**：`observability_closure_acceptance.yml`（调用 `roles/observability_acceptance`）
- **自动化单元与集成测试**：`tests/test_observability_closure_acceptance.py`

### 1.1 固定新鲜度门槛（Freshness Thresholds）

| 信号类别 | 默认新鲜度上限 | 说明 |
|---|---|---|
| 指标（Metrics / `victoriametrics`） | `<= 180s` | 不超过 3 个采样周期（默认 `60s * 3`） |
| 日志（Logs / `victorialogs`） | `<= 300s` | 不超过 5 分钟 |
| CMDB 资源事实（`cmdb_postgres`） | `<= 3600s` | 不超过既定同步周期（支持通过 `--cmdb-max-age` 按服务约定配置） |
| 连续性采样窗口（Continuity Window） | `>= 1800s` | 至少连续 30 分钟内相邻采样间隔 `<= 180s`，无断流缺口 |

### 1.2 核心门禁检查能力（全只读、零副作用）

1. **端点健康、版本与未认证边界核验（`endpoint_health` / `unauthenticated_boundary` / `datasources_contract`）**：
   - 仅执行 `GET /api/health`（要求 HTTP `200` 且 `database == "ok"`，记录 Grafana `version` 与 `commit`）及 `GET /login`（要求 HTTP `200`）；
   - 验证未认证边界：匿名 `GET /api/datasources` 必须返回 HTTP `401`/`403`；
   - 提供只读凭据时核验 4 个必需 Datasource UID：`victoriametrics`、`victorialogs`、`victoriatraces`、`cmdb_postgres`（缺凭据时严格标 `NOT_RUN`，缺 UID 时标 `FAIL`）。
2. **17 个 Dashboard 存在性、反伪造与查询新鲜度校验（`dashboards_17_coverage`）**：
   - 覆盖 `source-grafana-inventory` 中的 16 个迁入 Dashboard（`StarsL-JOB-node`、`agent-ai-observability`、`ai-aggregator-overview`、`begqoward2epsf`、`blackbox-exporter-overview`、`cmdb-resource-overview`、`homepage-navigation`、`k6-performance-capacity-overview`、`postgres-exporter-overview`、`process-exporter-with-tree`、`serverless-fullstack-architecture`、`victoria-logs-overview`、`victoria-traces-overview`、`xconnect-comprehensive`、`xconnect-user-connection-diagnostics`、`xhttp-edge-reliability`）加保留总览 `web-saas-k3s-v5-overview`，共 **17** 个；
   - **反伪造门禁（Anti-Masking Gate）**：递归扫描所有 Panel（含折叠行 `row` 子面板）查询，凡包含 `or vector(0)` 或 `or on(...) vector(0)` 掩盖缺数表达式的面板一律判定为 `FAIL`；
   - 按需压测面板 `k6-performance-capacity-overview` 在无压测任务运行且显式声明 idle/最后运行时间时判定为批准的 `N/A`。
3. **负向隔离校验（`negative_isolation`）**：
   - 故意使用不存在过滤条件 `resource_id="__nonexistent_obs_probe__"` 与 `environment="__invalid_env__"` 校验指标、日志与 CMDB 查询；
   - 必须返回严格空集（`0` 条序列/记录）；若返回非空数据则立即判定为 `FAIL`（跨环境/跨资源回退漏洞）。
4. **连续 30 分钟窗口与受控失联/恢复试验判定（`continuity_30m` / `controlled_recovery_drill`）**：
   - 校验至少连续 30 分钟（`1800s`）内采样点无 `> 180s` 断流缺口；
   - 当目标未获授权执行停止/恢复试验（`authorized: false`）时，严格输出 `NOT_RUN`（严禁伪造 `PASS`）；仅在获准窗口完成“停止 Agent → `<= 180s` 检出失联 → 启动 Agent → `<= 180s` 检出恢复”闭环实测时方可标为 `PASS`。
5. **结构化脱敏证据包输出**：
   - 自动清洗目标 IPv4（如 `104.10***213`）、Bearer/Basic Token 与密钥参数，输出 `evidence.json`、`report.zh.md` 与 `manifest.json`（SHA-256 校验清单）。

### 1.3 调用方式

```sh
# 1. 无凭据生产只读基线探测（输出脱敏证据包，缺凭据项严格标 NOT_RUN）
python3 scripts/verify_observability_closure.py \
  --environment prod \
  --target-url https://observability.svc.plus \
  --target-url https://observability-k3s-v5.svc.plus \
  --output-dir /tmp/obs-acceptance-bundle

# 2. 通过 Ansible Playbook 执行只读验收探针
ansible-playbook -i 'localhost,' -c local observability_closure_acceptance.yml \
  -e observability_acceptance_environment=prod \
  -e observability_acceptance_output_dir=/tmp/obs-acceptance-bundle
```

---

## 2. 生产环境只读诊断实测证据（2026-10-25 UTC）

- **执行时间（UTC）**：`2026-10-25T11:21:40.516481+00:00`（查询窗口 `2026-10-25T10:51:40Z` → `2026-10-25T11:21:40Z`）
- **执行方式**：仅发送无副作用 `GET` 请求探测公开 HTTPS 端点，未执行任何生产写入、未修改 DNS、未触碰生产业务容器。

| 目标站点 | 解析 IP（脱敏） | `GET /api/health` | `GET /login` | 数据库状态 | Grafana 版本 / Commit | 匿名 `GET /api/datasources` |
|---|---|---|---|---|---|---|
| `https://observability.svc.plus` | `104.10***213` | HTTP `200` | HTTP `200` | `ok` | `13.2.4` (`0e79aef9d9`) | HTTP `401`（鉴权边界正常拦截） |
| `https://observability-k3s-v5.svc.plus` | `104.10***213` | HTTP `200` | HTTP `200` | `ok` | `13.2.4` (`0e79aef9d9`) | HTTP `401`（鉴权边界正常拦截） |

### 各门禁当前状态与 `NOT_RUN` 清单

| 检查项 ID | 当前状态 | 证据与原因说明 |
|---|---|---|
| `endpoint_health` | `PASS` | 双域名 `/api/health` 与 `/login` 均返回 HTTP `200`，`database: ok`，Grafana `13.2.4` |
| `unauthenticated_boundary` | `PASS` | 双域名未认证访问 `/api/datasources` 均返回 HTTP `401 Unauthorized`，无匿名配置泄露 |
| `release_and_rollback_contract` | `PASS` | 分阶段依赖链完整，回滚红线已排除已销毁主机 `35.221.181.68` 及切 DNS/删数据操作 |
| `datasources_contract` | `NOT_RUN` | 当前会话未注入生产 Grafana 只读 API Token，需在获准验收阶段核验 `victoriametrics`、`victorialogs`、`victoriatraces`、`cmdb_postgres` |
| `dashboards_17_coverage` | `NOT_RUN` | 依赖 OBS-01/02/03/04 合入上线并注入生产只读 Token 后逐 UID 核验 17 个 Dashboard 查询与新鲜度 |
| `signal_freshness` | `NOT_RUN` | 依赖生产只读凭据核验 Metrics (`<=180s`)、Logs (`<=300s`)、CMDB (`<=3600s`) 实时样本延迟 |
| `negative_isolation` | `NOT_RUN` | 依赖生产只读凭据执行 `resource_id="__nonexistent_obs_probe__"` 与 `environment="__invalid_env__"` 空集校验 |
| `continuity_30m` | `NOT_RUN` | 依赖 OBS-01/02/03/04 上线后采集连续 30 分钟窗口（`1800s`）验证无断流缺口 |
| `controlled_recovery_drill` | `NOT_RUN` | 未获授权在生产执行 Agent 停止/恢复试验；按门禁规范严格保持 `NOT_RUN` |

---

## 3. 分阶段发布方案与回滚方案（Staged Release & Rollback Plan）

### 3.1 跨仓协同基线与依赖矩阵

| 阶段 / 任务包 | 所属仓库与 Issue | 分支 | 基线 `main` SHA | 核心交付与上游依赖 |
|---|---|---|---|---|
| **OBS-00** 契约基线 | `gitops#558` | `bugfix/558-obs-baseline` | `95a2c6f3112ba1cfe29c23b32253957484cafb97` | 17 Dashboard 全 Panel 清单、资源覆盖清单、统一资源键契约 |
| **OBS-01** CMDB/Serverless | `iac_modules#471` | `bugfix/471-obs-cmdb-serverless` | `3f049e5eae7cdd0358188dd318599a17176df04b` | 依赖 OBS-00；修复 Terraform/CMDB 权威事实同步与 Serverless 采集映射 |
| **OBS-02** 主机 Agent 对接 | `playbooks#766` | `bugfix/766-obs-host-agent` | `a7a01ac734968aa6e5f7baec0e172ae06a0b461d` | 依赖 OBS-00；对齐现有主机 Vector/Exporter 采集角色与统一资源标签 |
| **OBS-03** K3s/containerd 适配 | `artifacts#221` | `bugfix/221-obs-k3s-agent` | `a7ba34f38973d0777c1231200f8b85fcf52f5a9c` | 依赖 OBS-00；修复 K3s containerd 日志/指标采集与 Chart 打包并发布 Digest |
| **OBS-04** 17 Dashboard 对齐 | `gitops#559` | `bugfix/559-obs-dashboard-coverage` | `95a2c6f3112ba1cfe29c23b32253957484cafb97` | 依赖 OBS-00/01/02/03；修复生成源并重放 16 个迁入 Dashboard + K3s 总览 |
| **OBS-05** 集成验收与门禁 | `playbooks#767` | `bugfix/767-obs-acceptance` | `a7a01ac734968aa6e5f7baec0e172ae06a0b461d` | 提供可重用只读验收探针、门禁测试、生产只读诊断证据与发布/回滚方案 |

### 3.2 分阶段发布步骤（Six-Stage Release Workflow）

1. **阶段 1 — 锁定 OBS-00 公共契约（`gitops#558`）**：
   - 合入 `dashboard-coverage.csv`、`resource-coverage.csv` 及统一资源键规范（`provider+account/project+region+resource_id` 附加 `environment/service/cluster/namespace/node`），作为下游 Producer/Consumer 的唯一 schema 依据。
2. **阶段 2 — Producer 实现与不可变制品发布（OBS-01 / OBS-02 / OBS-03）**：
   - 合入 `iac_modules#471`（CMDB/Serverless 事实生产）、`playbooks#766`（主机 Agent 采集角色）与 `artifacts#221`（K3s/containerd 适配 Chart）；
   - CI 构建并发布 `oci://ghcr.io/ai-workspace-infra/observability-stack` 新 Chart digest，记录精确 source SHA 与 image/chart digest。
3. **阶段 3 — Consumer / Dashboard 对齐与 Bundle 重放（OBS-04，`gitops#559`）**：
   - 在生成源修复变量、PromQL/LogsQL/SQL 关联查询及跨面板跳转（严禁 `or vector(0)`），重放生成 `services/platform-observability/prod/imported-grafana/` ConfigMap bundle 与 `dashboards.yaml`，并 pin 新 Chart digest。
4. **阶段 4 — 测试/UAT 环境门禁验收**：
   - 在测试/UAT 环境执行 `observability_closure_acceptance.yml`（开启 `--require-pass`），验证 4 个 Datasource UID、17 个 Dashboard、新鲜度门槛（`<=180s` / `<=300s` / `<=3600s`）及负向隔离校验（`__nonexistent_obs_probe__` / `__invalid_env__` 返回空集）全部 `PASS`。
5. **阶段 5 — 授权后生产分批上线**：
   - 经人工审批放行后，先收敛 GitOps `platform-observability` HelmRelease/ConfigMap，再通过受控 workflow 按固定 Playbooks SHA 滚动同步主机 Agent 与 CMDB 采集；
   - **强约束**：全程不切 DNS、不删旧资源/数据、不扩大 Vault 权限、不改分支保护、不启用生产告警通知渠道。
6. **阶段 6 — OBS-05 连续 30 分钟运行验收与受控恢复试验（`playbooks#767` / `gitops#557`）**：
   - 生产收敛满 30 分钟后，运行 `scripts/verify_observability_closure.py --require-pass` 验证连续 30 分钟采样零断流缺口；
   - 在获准窗口对单台受控非核心节点执行 Agent 停止→`<=180s` 检出失联→恢复启动→`<=180s` 检出恢复试验，归档脱敏 `evidence.json` 与 `report.zh.md`，将剩余 `NOT_RUN` 清零后关闭父 Issue `gitops#557`。

### 3.3 回滚方案与安全红线（Rollback Plan & Redlines）

- **回滚红线（硬性禁止）**：
  1. **严禁回滚至已删除旧主机 `35.221.181.68`**：原主机 `35.221.181.68` 已彻底销毁，任何试图将 DNS、Agent 远端写入地址或回滚目标指向 `35.221.181.68` 的配置均被 `verify_observability_closure.py` 门禁直接阻断（`FAIL`）。
  2. **严禁影响 PROD 业务**：回滚严禁切换公网业务或监控 DNS、严禁删除 VictoriaMetrics/VictoriaLogs/VictoriaTraces/CMDB 存量数据、严禁旋转共享写入凭据或重启 Accounts/Billing/Gateway 等业务服务。
- **标准受控回滚操作**：
  1. **Dashboard / Grafana / K3s 观测栈回滚（GitOps）**：
     - 对 `gitops` 仓库执行 `git revert`，将 `services/platform-observability/prod/release.yaml` 的 `observability-stack-chart` digest 回退至基线 `sha256:6c9edd8ad04a06ec73ea0a6967e6653c4e1f1048487b88c7e5e9a43c077f7174`，并将 `imported-grafana` ConfigMap bundle 回退至基线 commit `95a2c6f3112ba1cfe29c23b32253957484cafb97`；
     - 等待 Flux 自动收敛 `platform-observability` HelmRelease，并使用 `verify_observability_closure.py` 核验 `/api/health` 与 `/login` 恢复 `PASS`。
  2. **主机 Agent 采集配置回滚（Playbooks）**：
     - 将调用方 pinned Playbooks SHA 回退至基线 `a7a01ac734968aa6e5f7baec0e172ae06a0b461d`，仅重放隔离的监控 Agent 角色（仅重启 `vector` / `cmdb-metrics-agent` / `cmdb-node-exporter`），不触碰主机业务进程。
  3. **CMDB / Serverless 采集回滚（IaC Modules）**：
     - 将 `iac_modules` 回退至基线 `3f049e5eae7cdd0358188dd318599a17176df04b`，保持只读同步状态，不销毁任何云资源或 Terraform state。
