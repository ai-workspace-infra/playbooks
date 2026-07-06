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

## install.svc.plus (xworkmate-bridge.svc.plus) 主机特例现状说明

因宿主机环境上存在生产遗留问题与容器端口占用冲突，针对 `install.svc.plus` 进行了特例微调，避开了占用端口并修改了证书配置。具体现状如下：

### 端口与服务变更
1. **停止 billing-service**：
   - 宿主机上的 `billing-service` 服务已被停止并禁用（`systemctl disable billing-service`），以释放 `127.0.0.1:8081` 端口给 `xray-exporter-tcp.service` 监听。
2. **微调 XHTTP 侧 API 端口**：
   - 宿主机上的 `console-c894924-contabo` 容器占用了 `127.0.0.1:18080`，因此将 `xray.service` (XHTTP) 的 API 端口微调为 `127.0.0.1:28080`。
   - `xray-exporter-xhttp.service` 同步修改上游探测地址为 `127.0.0.1:28080`。
3. **微调 TCP 侧 API 端口**：
   - 宿主机上的 `accounts-managed-prod-contabo` 容器占用了 `127.0.0.1:18081`，因此将 `xray-tcp.service` (TCP) 的 API 端口微调为 `127.0.0.1:28081`。
   - `xray-exporter-tcp.service` 同步修改上游探测地址为 `127.0.0.1:28081`。

### 证书路径修正
- `xray-tcp.service` 在该主机上加载的 TLS 证书路径修正为本地实际存在的 `xworkmate-bridge.svc.plus` 证书目录（`/var/lib/caddy/.local/share/caddy/certificates/.../xworkmate-bridge.svc.plus/`）。

### 调整后在该主机的端口拓扑
- `xray.service` (XHTTP API)：`127.0.0.1:28080`
- `xray-exporter-xhttp.service` (指标服务)：监听 `127.0.0.1:8080`
- `xray-tcp.service` (TCP API)：`127.0.0.1:28081`
- `xray-exporter-tcp.service` (指标服务)：监听 `127.0.0.1:8081`
- `vector.service` (指标抓取)：继续通过 `127.0.0.1:8080/scrape` 和 `127.0.0.1:8081/scrape` 正常抓取。

