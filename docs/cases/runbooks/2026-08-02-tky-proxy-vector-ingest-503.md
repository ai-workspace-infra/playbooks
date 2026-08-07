# tky-proxy Vector metrics ingest 503 与 node_exporter 短时 503

- 日期：2026-08-02
- 主机：`tky-proxy.svc.plus` (`43.207.194.92`)
- 关联服务：`vector.service`、`node-exporter.service`、`observability.svc.plus`
- 时间窗口：Vector UTC `2026-08-01 20:10–20:21`（主机本地 04:10–04:21）
- 变更范围：只读诊断后，重启客户端 `node-exporter` 与 `vector`；未重启服务端、未修改配置、未清理数据。

## 现象

Vector 的 `prometheus_remote_write` sink 多次收到 `HTTP 503 Service Unavailable` 并进入 retry；随后 `node_metrics` 对 `http://127.0.0.1:9100/metrics` 收到 HTTP 503。

## 证据链

1. 客户端 Vector 日志：
   - `2026-08-01T20:10:13Z`：remote write `503 Service Unavailable`，进入 retry。
   - `2026-08-01T20:10:20Z`：retry 日志因 flooding 被抑制。
   - `2026-08-01T20:21:03Z`：`node_metrics` 明确记录 `url=http://127.0.0.1:9100/metrics`、`error_code=http_response_503`。
2. 客户端配置 endpoint 为：
   `https://observability.svc.plus/ingest/metrics/api/v1/write`。
3. 服务端同一窗口之前出现磁盘写满：
   - `systemd-journald: Failed to create/open journal: No space left on device`
   - Docker：`failed to mount ... no space left on device`
   - rsyslog：`/var/log/syslog ... write error ... No space left on device`
   - 服务端 Vector 在 `2026-08-01T20:06:12Z` 也记录 remote write 503。
4. 服务端当前 VictoriaMetrics 容器仍在运行、无重启/OOM；本机 backend health/ready/write 分别返回 `200/200/204`，说明故障已恢复。
5. 客户端 `node_exporter` 当前连续请求返回 200；进程未因故障重启，未发现 OOM、磁盘满、inode 满或 textfile 文件异常。因此 node_exporter 的具体瞬时触发点未被历史日志证明，不能将其归因于配置错误。

## 根因与影响

### 已确认根因

`observability.svc.plus` 主机在事件期间短时耗尽可写磁盘空间，导致 Docker、journald、rsyslog 等写入失败，并使 metrics ingest 链路返回 503。当前磁盘已恢复可用，但应继续定位空间增长来源（Docker 日志/缓存/镜像或其他日志保留）。

### 未完全确认的次因

客户端 node_exporter 在 04:21:03 曾短时返回 503。当前证据只支持“瞬时 exporter handler/gather 异常”；客户端当时内存余量较低且 Vector cgroup 有 `memory.high` 事件，但无 OOM kill，故只能列为可疑压力因素。

### 影响

- Vector 进入 retry；其 remote write buffer 为 memory、上限 1000 events，重启或 buffer 满可能丢失未发送指标。
- node metrics 至少缺失一个 scrape 周期。
- 服务端日志 ingest 同期出现 429，存在独立限流压力。

## 修复动作

```sh
ssh admin@tky-proxy.svc.plus
sudo systemctl restart node-exporter vector
```

实际结果：

- `node-exporter` 新 PID `768593`，进入 `active (running)`。
- Vector 初次停止等待约 90 秒后自然完成重启；`ExecStartPre=/usr/bin/vector validate` 成功。
- Vector 新 PID `768674`，进入 `active (running)`。
- 重启后 `127.0.0.1:9100/metrics` 返回 `200`。
- 重启后远端 write endpoint 返回 `204`。

## 验证清单

```sh
systemctl is-active node-exporter vector
curl -fsS http://127.0.0.1:9100/metrics >/dev/null
curl -skS -o /dev/null -w '%{http_code}\n' \
  https://observability.svc.plus/ingest/metrics/api/v1/write
df -hT
df -ih
journalctl -u vector -n 100 --no-pager
```

## 回滚

本次仅重启服务，无配置或数据变更；无需文件回滚。若重启后 Vector 再次卡在停止阶段，不要强杀，先等待 systemd stop timeout，并保留 journal 供分析。

## 预防

- 为 observability 主机根分区、Docker 数据目录、Docker 日志和 journald 增加容量阈值告警。
- 明确 Docker 镜像、build cache、容器日志的保留与清理策略。
- 为客户端 Vector 监控 `memory.high`、内存 buffer 使用量和 remote write retry。
- 为 node_exporter 监控 scrape HTTP status、collector error 与进程资源；再次出现 503 时保留 exporter stderr/访问日志。
- 评估将 Vector buffer 改为可持久化磁盘 buffer 前的容量、恢复和数据保留策略。
