# 重复 EXPORTER_NODE_ID 引发 Checkpoint 乒乓循环与实时账单虚增（75.82 GiB / 482.84 GiB）

- 日期：2026-11-23
- 涉及主机：`jp-xconnect-01.svc.plus`、`jp-xconnect-02.svc.plus`、PROD 控制面（`prod-k3s-v6` / PostgreSQL `account`）
- 关联服务：`xray-exporter-xhttp.service`、`xray-exporter-tcp.service`、`vector.service`、`billing-service`、`accounts`
- 关联代码与 PR：
  - `ai-workspace-infra/playbooks#1046`（强制 `EXPORTER_NODE_ID == AGENT_PROXY_DOMAIN`）
  - `ai-workspace-xstream/xconnect-edge-agent#167`（`setup-observability.sh` 显式透传唯一 `agent_proxy_domain` / `xray_exporter_node_id`）
- 数据备份表：`public.traffic_minute_buckets_backup_20261123`（2026-11-01 起共 `278,619` 行）

## 现象

1. Portal / `accounts.svc.plus` 实时用量面板（数据源 PostgreSQL `traffic_minute_buckets`）显示账号 `691c5728-be84-48da-b8f6-37418a57e5e8`：
   - **最近 24 小时：`75.82 GiB`**（随后滚存至 `76.16 GiB`）
   - **本月合计：`482.84 GiB`**（随后滚存至 `483.68 GiB`）
   - 在 `2026-11-22 17:06 UTC` 触发 `50 GiB` 月配额耗尽（`remaining_included_quota = 0`）。
2. 连带表现为：**macOS 客户端连接 `jp-xconnect-01.svc.plus` 异常/断开**，而手机端 APP（落到 `jp-xconnect-02.svc.plus`）仍可连通。
3. 本机客户端统计远小于账单计数，核查 `jp-xconnect-01` 与 `jp-xconnect-02` 最近 24 小时原始 Xray 增量合计仅约 `21.78 GiB`（整月亦显著低于 `482.84 GiB`）。

> **排查防误判提示**：看到某小时/某分钟突增 `1 GiB+` 时，不要直接归因于“用户大流量下载”或 `billing-service` 算错乘法；先检查 `traffic_stat_checkpoints.reset_epoch` 与单分钟 `uplink_bytes` / `downlink_bytes` 是否出现高水位平台连跳（每分钟均出现相同数值的 `>100 MiB` 记录）。

## 证据链

1. **数据库分钟桶平台连跳与高重置次数（PROD PostgreSQL）**：
   - `traffic_stat_checkpoints` 中，`prod:jp-xconnect-02.svc.plus-xhttp` 的 `reset_epoch = 98`（同节点簇历史 `prod:jp-xconnect.svc.plus-xhttp` 甚至高达 `687`、`prod:jp-xconnect-01.svc.plus-xhttp` 达 `115`）。
   - `2026-11-22 16:41 ~ 17:06 UTC` 连续 26 分钟内，`node_id = 'prod:jp-xconnect-02.svc.plus-xhttp'` 每分钟都在 `:47.8s` 写入几乎一模一样的单分钟流量：
     - `uplink_bytes ≈ 189,774,303`、`downlink_bytes ≈ 942,600,595`、`total_bytes ≈ 1,132,374,898`（`1079.9 MiB` / 分钟）。
2. **两台物理机原始快照核对（`/var/lib/xray-exporter/xhttp-snapshots.json`）**：
   - 在同一时间窗 `2026-11-22 16:59 UTC`：
     - `jp-xconnect-01.svc.plus` 上报累计值：`uplink = 193,542,612`、`downlink = 983,366,395`（合计 `1,176,909,007 B`），但快照里的 `"node_id"` 字段写的是 **`jp-xconnect-02.svc.plus-xhttp`**。
     - `jp-xconnect-02.svc.plus` 上报累计值：`uplink = 3,768,309`、`downlink = 40,765,800`（合计 `44,534,109 B`），快照 `"node_id"` 也是 **`jp-xconnect-02.svc.plus-xhttp`**。
     - 两者差值恰好等于数据库写入的单分钟扣费：
       `1,176,909,007 - 44,534,109 = 1,132,374,898 Bytes`。
3. **节点配置对比**：
   - 检查全量节点 `/etc/xray-exporter.env` 与 `/etc/systemd/system/xray-exporter-{xhttp,tcp}.service`：
     - `jp-xconnect-01.svc.plus` 的 `/etc/xray-exporter.env` 为 `EXPORTER_NODE_ID=jp-xconnect-02.svc.plus`，且 systemd `ExecStart` 中 `--node-id` 也为 `jp-xconnect-02.svc.plus-{xhttp,tcp}`（文件时间 `Nov 8 23:09`）。
     - 其余 5 台在线节点（`jp-xconnect-02`、`jp-xconnect-free-01`、`ph-xconnect`、`us-xconnect-los-angeles-custom-01`、`us-xconnect`）均与各自 `AGENT_PROXY_DOMAIN` 一致。
4. **macOS 与手机端表现差异原因**：
   - 虚假计费在 `2026-11-22 17:06:47 UTC` 将 `remaining_included_quota` 扣至 `0`：
     - `accounts` 下发 `HTTP 403 quota_exhausted`，触发 macOS `DesktopSyncService` 的 `_stopManagedAccess('quotaPaused')` 停用托管节点，同时 `jp-xconnect-01` 上的 `xconnect-edge-agent` 将用户 UUID 从 `/usr/local/etc/xray/config.json` 移除（`/var/log/xray/access.log` 报错 `rejected proxy/vless/encoding: invalid request user id`）。
     - 而 `jp-xconnect-02.svc.plus` 在 `/etc/agent/account-agent.yaml` 中保留了 `allowedEmails: ["156405189@qq.com"]` 白名单兜底，因此手机端仍能连通 `jp-02`。
     - 此外 `jp-xconnect-01.svc.plus:1443`（`xray-tcp.service`）被云安全组拦截超时，仅 `443`（HAProxy XHTTP `/split`）公网可达。

## 根因

1. **直接根因（多物理生产者共用同一 `node_id`）**：
   - `jp-xconnect-01.svc.plus` 与 `jp-xconnect-02.svc.plus` 的 `xray-exporter` 使用了相同的 `EXPORTER_NODE_ID=jp-xconnect-02.svc.plus`，导致两台机器每分钟向 `billing-service` 写入同一个 `traffic_stat_checkpoints` 主键 `(prod:jp-xconnect-02.svc.plus-xhttp, account_uuid)`。
   - 根据 [`billing-service/internal/service/service.go`](https://github.com/ai-workspace-services/billing-service/blob/main/internal/service/service.go) 的 `processSampleLocked` 逻辑：
     1. 累计值较小节点（`jp-02`）推送时，`delta < 0`，触发“计数器重置”分支（`reset_epoch++`），将 `traffic_stat_checkpoints` 改写为较小累计值后提前返回；
     2. 约 30 秒后累计值较大节点（`jp-01`）推送时，`delta = 较大累计值 - 较小累计值 > 0`，`billing-service` 将整段差值作为该分钟的新增流量写入 `traffic_minute_buckets` 并扣减配额；
     3. 下一分钟循环往复，造成**每分钟重复扣减整段历史累计差值**的乒乓放大效应（`72.39 GiB / 76.16 GiB` 仅在 24 小时内即为该循环制造）。
2. **体制性隐患**：
   - `roles/vhosts/xray-exporter/defaults/main.yml`、`deploy_observability_agent.yml`、`deploy_xray_exporter.yml` 原先从 `inventory_hostname` 推导 `xray_exporter_node_id`，且未校验其必须与 `AGENT_PROXY_DOMAIN` 完全一致，也未写入 `AGENT_PROXY_DOMAIN` 到 `/etc/xray-exporter.env` 作为对照锚点。

## 修复

### 1. 在线节点立即对齐 `AGENT_PROXY_DOMAIN == EXPORTER_NODE_ID`
在 `jp-xconnect-01.svc.plus` 上停用 `xray-exporter-{xhttp,tcp}`，修正：
- `/etc/xray-exporter.env`：`AGENT_PROXY_DOMAIN=jp-xconnect-01.svc.plus` 与 `EXPORTER_NODE_ID=jp-xconnect-01.svc.plus`。
- `/etc/systemd/system/xray-exporter-{xhttp,tcp}.service`：`--node-id jp-xconnect-01.svc.plus-{xhttp,tcp}` 并 `systemctl daemon-reload`。
- 重写本地最近 72h 缓存快照 `/var/lib/xray-exporter/{xhttp,tcp}-snapshots.json` 中的 `node_id` 为 `jp-xconnect-01.svc.plus-{xhttp,tcp}`，并在 PROD PostgreSQL `traffic_stat_checkpoints` 中先同步 `prod:jp-xconnect-01.svc.plus-xhttp` 的当前累计水位，再重启 exporter，防止首次重启把历史累计值再次计为单分钟增量。

### 2. 数据备份、精确还原与配额重算（PROD DB）
1. 创建备份表：`public.traffic_minute_buckets_backup_20261123`（共 `278,619` 行）。
2. 针对快照留存窗口（`2026-11-19 23:47 UTC ~ 2026-11-23 00:05 UTC`），从 `jp-xconnect-01` 与 `jp-xconnect-02` 两台节点本机 `xhttp-snapshots.json` 逐分钟重算单调增量（`18,820` 行），替换碰撞期间写入的 `20,444` 条混合桶记录。
3. 针对 11 月更早期已无原始快照的历史记录，在 `traffic_minute_buckets` 上按 `(node_id, account_uuid)` 的 15 分钟前序高水位窗口执行去重（共修正 `10,859` 行异常乒乓/跳变行）。
4. 重算全部 `41` 个账户的 `account_quota_states` 剩余配额。

### 3. 代码与编排固化（已合并 `main`）
- `playbooks`（PR `#1046`）：`xray_exporter_node_id` 默认绑定 `AGENT_PROXY_DOMAIN`，模板写入 `AGENT_PROXY_DOMAIN`，任务渲染前 `assert` 非空、非 `localhost` 且与 `AGENT_PROXY_DOMAIN` 相等。
- `xconnect-edge-agent`（PR `#167`）：`scripts/setup-observability.sh` 显式将 `AGENT_PROXY_DOMAIN` 传入 Ansible `vars.json` 的 `agent_proxy_domain` 与 `xray_exporter_node_id`。

## 验证清单

### 1. 节点唯一性与服务检查（全量代理节点执行）
```sh
for host in \
  jp-xconnect-01.svc.plus \
  jp-xconnect-02.svc.plus \
  jp-xconnect-free-01.svc.plus \
  ph-xconnect.svc.plus \
  us-xconnect-los-angeles-custom-01.svc.plus \
  us-xconnect.svc.plus; do
  echo "=== $host ==="
  ssh root@$host '
    grep -E "^(EXPORTER_NODE_ID|AGENT_PROXY_DOMAIN|EXPORTER_ENV)=" /etc/xray-exporter.env
    grep -h "ExecStart=" /etc/systemd/system/xray-exporter*.service
    systemctl is-active xray-exporter-xhttp xray-exporter-tcp vector
  '
done
```
实测结果：6 台节点的 `AGENT_PROXY_DOMAIN` 与 `EXPORTER_NODE_ID` 完全一致且唯一，服务全部 `active`。

### 2. PROD PostgreSQL 修复前后比对与增量入库检查
```sql
-- 1) 检查是否存在最近重置尖刺
SELECT node_id, count(*), max(reset_epoch) AS max_reset_epoch, max(updated_at)
FROM traffic_stat_checkpoints
GROUP BY node_id
ORDER BY max_reset_epoch DESC;

-- 2) 检查账号修复后用量（1h / 24h / 本月）
SELECT COALESCE(node_id, 'TOTAL') AS node_id,
       ROUND(COALESCE(SUM(total_bytes) FILTER (WHERE bucket_start >= now() - interval '1 hour'), 0) / 1073741824.0, 4) AS gib_1h,
       ROUND(COALESCE(SUM(total_bytes) FILTER (WHERE bucket_start >= now() - interval '24 hours'), 0) / 1073741824.0, 4) AS gib_24h,
       ROUND(SUM(total_bytes) / 1073741824.0, 4) AS gib_month
FROM traffic_minute_buckets
WHERE account_uuid = '691c5728-be84-48da-b8f6-37418a57e5e8'
  AND bucket_start >= '2026-10-31 16:00:00+00'
GROUP BY ROLLUP(node_id)
ORDER BY gib_month DESC NULLS FIRST;
```
实测修复结果（账号 `691c5728-be84-48da-b8f6-37418a57e5e8`）：
- **最近 1 小时**：`1.11 GiB`（已拆分为 `prod:jp-xconnect-01.svc.plus-xhttp` 独立入库）
- **最近 24 小时**：由 `75.82 GiB` 降至 **`20.65 GiB`**（消除虚增 `-55.17 GiB`）
- **本月合计**：由 `482.84 GiB` 降至 **`205.19 GiB`**（消除虚增 `-277.65 GiB`）
- **配额余量**：VPS 流量余量 **`48.94 GiB`** / 高速流量余量 **`30.00 GiB`**（`throttle_state=normal`、`suspend_state=active`）

## 回滚

若发现历史桶重算阈值误伤正常数据，可在同一事务中用备份表恢复 11 月数据并重算配额：
```sql
BEGIN;
DELETE FROM public.traffic_minute_buckets
WHERE bucket_start >= '2026-11-01 00:00:00+00'
  AND bucket_start <= (SELECT max(bucket_start) FROM public.traffic_minute_buckets_backup_20261123);

INSERT INTO public.traffic_minute_buckets
SELECT * FROM public.traffic_minute_buckets_backup_20261123;
COMMIT;
```
节点侧 `/etc/xray-exporter.env` 与 systemd unit 不得回滚为重复 `node_id`。

## 预防

1. **配置单一事实源**：任何新节点上线或重装时，`/etc/xray-exporter.env` 中的 `AGENT_PROXY_DOMAIN`、`EXPORTER_NODE_ID` 以及 `/etc/agent/conf.d/*.yaml` 的 `agent.id` 必须严格一致，禁止复制邻近节点的 `.env` 或 systemd unit 文件。
2. **监控告警抓手**：
   - 当同一 `(node_id, account_uuid)` 的 `traffic_stat_checkpoints.reset_epoch` 在 10 分钟内连续递增超过 `2` 次，或单分钟 `total_bytes > 500 MiB` 且连续分钟数值方差 `< 20%` 时，立即告警排查是否存在双机共用了同一 `EXPORTER_NODE_ID`。
3. **跨节点清理策略**：未来将域名/节点配置迁移到另一台新 ECS 时，必须先在旧节点停用 `xray-exporter-*` 与 `vector`，或给新旧节点分配互不相同的 `AGENT_PROXY_DOMAIN` / `EXPORTER_NODE_ID`。
