# svc-plus-20260705-repair-snapshot 环境说明

这个文档记录 `svc-plus-20260705-repair-snapshot` 对应的最终线上口径，方便后续迁移、部署和回滚时对照。

## 最终版端口拓扑

| 组件 | 监听地址 | 说明 |
| --- | --- | --- |
| `xray-exporter-xhttp.service` | `127.0.0.1:8080` | 采集 `xray.service` 的上游指标 |
| `xray-exporter-tcp.service` | `127.0.0.1:8081` | 采集 `xray-tcp.service` 的上游指标 |
| `xray.service` | `127.0.0.1:18080` | XHTTP 侧 Xray API 端口 |
| `xray-tcp.service` | `127.0.0.1:18081` | TCP 侧 Xray API 端口 |
| `node_exporter` | `127.0.0.1:9100` | 主机指标采集 |
| `process_exporter` | `127.0.0.1:9256` | 进程指标采集 |

## systemd 关系

### Xray

- `xray.service`
  - 使用 `/usr/local/etc/xray/config.json`
  - 提供 XHTTP 侧的 `StatsService`
  - API 监听 `127.0.0.1:18080`

- `xray-tcp.service`
  - 使用 `/usr/local/etc/xray/tcp-config.json`
  - 提供 TCP 侧的 `StatsService`
  - API 监听 `127.0.0.1:18081`

### Exporter

- `xray-exporter-xhttp.service`
  - 监听 `127.0.0.1:8080`
  - 上游指向 `127.0.0.1:18080`

- `xray-exporter-tcp.service`
  - 监听 `127.0.0.1:8081`
  - 上游指向 `127.0.0.1:18081`

### 主机观测

- `node_exporter`
  - 只绑定 `127.0.0.1:9100`

- `process_exporter`
  - 只绑定 `127.0.0.1:9256`

## Vector 采集链路

Vector 采用双源采集 Xray 指标：

- `http://127.0.0.1:8080/scrape`
  - 采集 `xray-exporter-xhttp.service`
  - 打上 `transport="xhttp"`

- `http://127.0.0.1:8081/scrape`
  - 采集 `xray-exporter-tcp.service`
  - 打上 `transport="tcp"`

同时保留主机侧采集：

- `http://127.0.0.1:9100/metrics`
  - `node_exporter`

- `http://127.0.0.1:9256/metrics`
  - `process_exporter`

最终由 Vector 统一写入远端 Prometheus Remote Write。

## 对应配置文件

- [`roles/vhosts/agent-svc-plus/defaults/main.yml`](/Users/shenlan/workspaces/ai-workspace-infra/playbooks/roles/vhosts/agent-svc-plus/defaults/main.yml)
- [`roles/vhosts/agent-svc-plus/templates/xray.service.j2`](/Users/shenlan/workspaces/ai-workspace-infra/playbooks/roles/vhosts/agent-svc-plus/templates/xray.service.j2)
- [`roles/vhosts/agent-svc-plus/templates/xray-tcp.service.j2`](/Users/shenlan/workspaces/ai-workspace-infra/playbooks/roles/vhosts/agent-svc-plus/templates/xray-tcp.service.j2)
- [`roles/vhosts/agent-svc-plus/templates/xray.xhttp.template.json.j2`](/Users/shenlan/workspaces/ai-workspace-infra/playbooks/roles/vhosts/agent-svc-plus/templates/xray.xhttp.template.json.j2)
- [`roles/vhosts/agent-svc-plus/templates/xray.tcp.template.json.j2`](/Users/shenlan/workspaces/ai-workspace-infra/playbooks/roles/vhosts/agent-svc-plus/templates/xray.tcp.template.json.j2)
- [`roles/vhosts/xray-exporter/defaults/main.yml`](/Users/shenlan/workspaces/ai-workspace-infra/playbooks/roles/vhosts/xray-exporter/defaults/main.yml)
- [`roles/vhosts/xray-exporter/templates/xray-exporter.service.j2`](/Users/shenlan/workspaces/ai-workspace-infra/playbooks/roles/vhosts/xray-exporter/templates/xray-exporter.service.j2)
- [`roles/vhosts/vector-agent/templates/vector.toml.j2`](/Users/shenlan/workspaces/ai-workspace-infra/playbooks/roles/vhosts/vector-agent/templates/vector.toml.j2)

## 备注

- 这版口径以 `svc-plus-20260705-repair-snapshot` 为准。
- 旧的单实例 `xray-exporter-bin.service` 已废弃。
- 这版配置的目标是让 XHTTP / TCP 两条链路可以独立启动、独立采集、独立观测。
