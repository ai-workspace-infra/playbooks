# qmd_hub — QMD Hub（第 4 级企业持久存储模式 + Hub-Spoke 中心）

Baseline: [ai-workspace-services/knowledge#400](https://github.com/ai-workspace-services/knowledge/pull/400)
— `docs/reference/platform-delivery/architecture/QMD单体运行与分布式异步同步架构规范.md`
与 `docs/reference/qmd-monolithic-engine-distributed-async-sync-spec.zh.md`。

## 唯一职责

在一台 Debian x86_64 主机上以独立系统用户、独立目录、独立 systemd 单元运行 QMD Hub：

| 层 | 实现 | 位置 |
|---|---|---|
| L1 热层 | `ai-workspace-lab/qmd`（固定 commit）单体 QMD + SQLite（FTS5），`qmd mcp --http` | `127.0.0.1:8181`（`/mcp` `/query` `/search` Bearer） |
| L2 权威轻元数据 | 专用 PostgreSQL 17 + pgvector 集群（仅 127.0.0.1），fence 日志 `qmd_hub_changes`，QMD PG backend（`qmd pg/memory/tier`） | `127.0.0.1:5433`，库 `qmd_memory` |
| L3 冷归档 | `qmd-hub-archive.timer` 每日一次，rclone `copy --ignore-existing`，只上传新 `.md`，不原地改 | `gdrive` / `s3` / `none` |
| Hub-Spoke Delta Sync | `qmd-hub-syncd`（零依赖 Node）：`/plan` `/put` `/commit` `/changes?since_fence` `/file` `/events`(SSE)，`/auth` 供 Traefik forwardAuth；对外域名所有路径未认证一律 401 | 仅绑定 CNI 网桥 `10.42.0.1:8182`；对外经 Traefik TLS（`https://qmd.svc.plus`，GitOps 声明） |

它不管理 k3s、Flux、Ingress/DNS（由 GitOps/IaC 声明），也不读取或分发任何个人凭据。

## 输入（defaults/main.yml）

- `qmd_hub_version`：40 位 commit（必填固定）；`qmd_hub_source_repo`：HTTPS 公共仓库，无需 deploy key。
- `qmd_hub_pg_enabled`（默认 true）、`qmd_hub_pg_port`（5433）、`qmd_hub_pg_database`、`qmd_hub_pg_role`。
- `qmd_hub_archive_backend`：`gdrive|s3|none`；`qmd_hub_archive_remote`、`qmd_hub_archive_path`、`qmd_hub_archive_on_calendar`。
- `qmd_hub_sync_enabled`、`qmd_hub_sync_listen_host`（CNI 网桥 IP，禁止公网 IP）、`qmd_hub_sync_allow_from`（ufw 源，仅 Pod CIDR）。
- `qmd_hub_spoke_authorized_keys`：Spoke 公钥（仅允许 `permitopen` 到同步 API 的 SSH 隧道兜底，无 shell/pty）。

## 前置条件与秘密

- 角色在主机上生成 `/etc/qmd-hub/{mcp-token,sync-token,pg-password}`（`qmdhub:qmdhub 0600`），值不回传控制端、不进日志；轮换 = 删除文件后重跑。
- L3 凭据不由角色创建：提供 `/etc/qmd-hub/rclone.conf`（`qmdhub` 所有，`0600`），包含 remote `[qmd-archive]`（Drive 服务账号或 OAuth；或 S3）。缺失时 timer 照常运行并写 `state/archive.status = credentials-missing`。
- Spoke 侧 token 需带外分发到 Spoke 的 `0600` 文件（例如 `~/qmd-spoke/hub-token`）。

## 输出 / 验收证据

`tasks/verify.yml`：`/health` 200、无 token `/query` 401、同步 API `/health` 200 与无 token 401、首次 `qmd-hub-ingest`、`qmd status`。
`qmd pg status` 应显示 `PostgreSQL 17.x · +pg_trgm +pgvector`。

## 幂等、重试与回退

- 再次运行只在 commit/模板变化时重建或重启；`--check` 首次安装仅做前置校验后结束（后续任务依赖已创建的用户与目录）。
- Ingest 幂等：内容 sha256 未变不分配新 fence；`/commit` 前校验全部文件哈希，缺失返回 409，客户端重试只补传缺失文件。
- 回退：`systemctl disable --now qmd-hub qmd-hub-syncd qmd-hub-archive.timer`；`pg_dropcluster 17 qmdhub --stop`；删除 `/opt/qmd-hub` `/etc/qmd-hub`、ufw 规则 `qmd-hub-syncd`。不影响 k3s 与集群内 PostgreSQL。

## 与规范的差异（子集说明）

- 规范示例中的 `QMD_ROLE=hub` / `qmd-hub --daemon` / `POST /api/v1/sync/push` 在 qmd 仓库中不存在；本角色以 `qmd-hub-syncd` 实现等价的 Delta API（`/plan`+`/put`+`/commit`），fence 由 PG `bigserial` 分配。
- Hub 侧集中 Embedding 未启用：主机无 GPU/内存余量，未配置 `QMD_EMBED_API_*`，当前为 BM25（FTS5 / PG FTS），向量片段不随增量下发。
- 同步 token 为单一共享 token（未按 Spoke 区分）；冲突策略为“每个 origin 只写自己的命名空间、同 key 以最高 fence 为准”，未实现跨 origin 合并。
- L3 走 rclone，不复用 qmd `tier l3`（其 L3 交接依赖 box 的 `.archive`/UploadFile 流程）。

## 入口认证（与 desktop.svc.plus 同一模式，共享 PROD accounts.svc.plus）

- 对外 `https://qmd.svc.plus` 每条路径（含 `/health`）先经 Traefik forwardAuth → `qmd-hub-syncd /api/v1/sync/auth`；未认证 401（浏览器 `Accept: text/html` 时 401 页附 `qmd_hub_login_url` 登录链接）。
- 人类：PROD `accounts.svc.plus`（`prod-k3s-v6.svc.plus` 提供）会话 —— `Authorization: Bearer <session>` 或 `xc_session` cookie（域 `.svc.plus`），跨集群调用 `{{ qmd_hub_accounts_url }}/api/auth/session` 校验，结果缓存 `qmd_hub_auth_cache_seconds`；并按 `qmd_hub_allowed_roles` / `qmd_hub_allowed_emails` 限定。
- 机器：Spoke 同步 token（Hub 本机 0600 `sync-token`）照常有效，后端对每个请求再次校验同一凭据。
- 不需要在 accounts 侧注册 OAuth client/回调（与 xworkmate-remote-desktop 一样只调用会话校验端点）。
