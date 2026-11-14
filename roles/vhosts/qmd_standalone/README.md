# qmd_standalone — QMD 单机 / Spoke（第 1 级：QMD + SQLite + sync）

Baseline: [ai-workspace-services/knowledge#400](https://github.com/ai-workspace-services/knowledge/pull/400)
— `docs/reference/platform-delivery/architecture/QMD单体运行与分布式异步同步架构规范.md`。

## 唯一职责

以登录用户（无 root）安装固定 commit 的 `ai-workspace-lab/qmd`，使用隔离的 `QMD_CONFIG_DIR`/`INDEX_PATH`（`~/qmd-spoke`），
建立 collections 并索引；可选与 QMD Hub 配对，安装一条双向异步同步命令。不安装系统包（需已有 Node ≥ 22）。

## 输入

- `qmd_standalone_collections`：`name -> {path, pattern, context, push}`，`push: true` 进入 Spoke Outbox。
- `qmd_standalone_hub_url`（`https://`）、`qmd_standalone_hub_token_file`（0600，带外提供）、`qmd_standalone_hub_transport`（`https|tunnel|auto`）。
- `qmd_standalone_hub_tunnel_ssh` / `qmd_standalone_hub_tunnel_target` / `qmd_standalone_hub_ssh_key`：HTTPS 不可达时的受限 SSH 端口转发兜底。
- `qmd_standalone_sync_link`：调度器使用的固定路径（例如 `/workspace/qmd-sync/sync.sh`）。

## 同步语义（`qmd-spoke-sync`）

1. 可选 `git pull --ff-only` 刷新只读源；本地 `qmd update`。
2. **push**：Outbox = 本地 manifest 与上次成功推送 manifest 的差异；空则跳过。`/plan` 返回 Hub 缺失/不同的文件，仅上传这些（4 MiB 分批），`/commit` 校验哈希后由 Hub 分配 fence。
3. **pull**：`/changes?since_fence=N&exclude_origin=<self>`，同 key 取最高 fence；按 sha256 判断是否需要拉取，原子写入只读 `mirror/<origin>/<collection>/`，删除事件同步删除；全部校验通过才推进 `state/since_fence`，否则保留并在下次重试。
4. `--watch` 订阅 `/events`（SSE），fence 变化即触发一次同步。

## 验收

`qmd status`；`sync.sh` 输出 JSON（push fence / pull since_fence）；Hub 上 `qmd search <spoke 标记>` 命中，Spoke 上 `qmd search <hub 标记>` 命中 `hub-mirror`。

## 回退

删除 `~/qmd-spoke` 与同步链接；Hub 侧数据按 origin 目录独立，可在 Hub 删除 `data/origins/<origin>` 后运行 `qmd-hub-ingest <origin>` 记录删除。
