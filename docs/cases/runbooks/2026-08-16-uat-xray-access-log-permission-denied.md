# UAT agent-proxy Xray 因共享 access.log 权限错误未启动

- 日期：2026-08-16
- 主机：`agent-proxy.onwalk.net`
- 关联服务：`xray.service`（XHTTP）、`xray-tcp.service`（TCP）、`xray-exporter`、UAT Console usage API
- 影响：Console 面板显示用量、配额和订阅数据均为零或空；Grafana `xray_up` 为 `0`
- 变更范围：本次先完成只读诊断；修复通过本仓库 PR 发布，未直接修改主机
- 角色命名：本次 PR 将旧 `agent-svc-plus` role 统一为 `agent-proxy`；上游 `agent.svc.plus` 仓库和 release 资产名保持不变

## 现象

两个 Xray systemd 服务在重启后立即退出，usage exporter 仍在监听但无法采集有效的 Xray 数据：

```text
Failed to start: ... failed to initialize access logger >
open /var/log/xray/access.log: permission denied
```

对应指标为 `xray_up 0`、`xray_total_connections 0`、`xray_unique_users 0`。

## 证据链

1. `xray.service` 以 `nobody` 运行，`xray-tcp.service` 以 `caddy` 运行；两者都需要追加写入 `/var/log/xray/access.log`。
2. 主机上的 `nobody` 和 `caddy` 都已属于 `xray-access` 组；该组为 `agent-proxy` role 创建，并由该 role 将两个服务用户加入组。
3. `agent-proxy` role 原本将共享日志设置为 `root:xray-access`、`0660`，因此服务用户通过组权限可写。
4. `setup-agent-proxy-domain.yml` 先导入 Xray 服务，再导入 Xray exporter。后续 exporter task 曾以“确保有读权限”为名，将同一个文件强制改成 `0644`。
5. `0644` 去除了组写权限；Xray 进程虽仍在 `xray-access` 组中，但无法打开 access log 进行追加，两个服务因此以 status `23` 失败。

## 根因与影响

### 已确认根因

`roles/vhosts/xray-exporter/tasks/main.yml` 对 agent-proxy 管理的共享日志错误地设置了 `0644`，覆盖了 `agent-proxy` role 建立的 `0660` 组写权限。问题不是 `xray-access` 组未引入：该组及成员关系已经正确；根因是 exporter role 破坏了共享文件的权限契约。

### 影响

- XHTTP 与 TCP 两个 Xray 服务均无法启动。
- exporter 的 scrape endpoint 可访问，但报告 `xray_up 0`，没有连接和用户数据。
- Console usage API 因没有新的 usage snapshot，展示 `0 B`、`0.0%` 和空订阅记录。

## 修复

exporter role 不再降级共享日志权限，而是显式保持：

```yaml
owner: root
group: xray-access
mode: "0660"
```

`xray-access` 的创建和 `nobody`/`caddy` 成员关系继续由 `agent-proxy` role 负责，避免两个 role 各自管理同一权限契约的不同部分。

## 验证清单

部署 PR 后在目标主机执行：

```sh
stat -c '%U:%G %a %n' /var/log/xray/access.log
systemctl is-active xray xray-tcp
ss -lntp | grep -E '28080|28081'
curl -s http://127.0.0.1:8080/scrape | grep xray_up
curl -s http://127.0.0.1:8081/scrape | grep xray_up
```

预期文件为 `root:xray-access 660`，两个服务为 `active`，两个 exporter 实例的 `xray_up` 为 `1`。

## 预防

- 共享文件的 owner、group 和 mode 由拥有该文件写入契约的 role 统一定义；消费者 role 只能校验或保持，不得降级权限。
- 为 Xray 服务启动、`xray_up` 和共享 access log 权限增加部署后探针。
- 修改 exporter 日志权限时，同时检查 agent-proxy 的服务用户、组和日志初始化任务。
