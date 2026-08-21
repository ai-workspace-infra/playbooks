# svc-plus-20260705-repair-snapshot 环境说明

这个文档记录 `svc-plus-20260705-repair-snapshot` 对应的最终线上口径，方便后续迁移、部署和回滚时对照。

## all-in-one 端口隔离约定

all-in-one 主机可能同时承载多个本地服务，`10080/10081` 与
`18080/18081` 已被其他服务或宿主机编排预留，不能再作为 Xray Stats API
端口使用。基础设施 playbook 的当前规范如下：

| 用途 | XHTTP | TCP |
| --- | ---: | ---: |
| 其他 all-in-one 服务预留 | `10080` / `18080` | `10081` / `18081` |
| Xray Stats API | `28080` | `28081` |
| xray-exporter 指标端点 | `8080` | `8081` |

因此，`xray.service` 与 `xray-tcp.service` 必须分别监听
`127.0.0.1:28080` 和 `127.0.0.1:28081`，对应 exporter 也必须使用相同的
上游地址。不要恢复 `28181`，也不要把 exporter 指向 `18080/18081`；否则
all-in-one 部署会发生端口冲突或 exporter 连接拒绝。

该约定由以下 playbook 默认变量共同保证：

- `roles/vhosts/agent-proxy/defaults/main.yml`
- `roles/vhosts/xray-exporter/defaults/main.yml`
- `deploy_xray_exporter.yml`

## 最终版端口拓扑

| 组件 | 监听地址 | 说明 |
| --- | --- | --- |
| `xray-exporter-xhttp.service` | `127.0.0.1:8080` | 采集 `xray.service` 的上游指标 |
| `xray-exporter-tcp.service` | `127.0.0.1:8081` | 采集 `xray-tcp.service` 的上游指标 |
| `xray.service` | `127.0.0.1:28080` | XHTTP 侧 Xray API 端口 |
| `xray-tcp.service` | `127.0.0.1:28081` | TCP 侧 Xray API 端口 |
| `node_exporter` | `127.0.0.1:9100` | 主机指标采集 |
| `process_exporter` | `127.0.0.1:9256` | 进程指标采集 |

## systemd 关系

### Xray

- `xray.service`
  - 使用 `/usr/local/etc/xray/config.json`
  - 提供 XHTTP 侧的 `StatsService`
  - API 监听 `127.0.0.1:28080`

- `xray-tcp.service`
  - 使用 `/usr/local/etc/xray/tcp-config.json`
  - 提供 TCP 侧的 `StatsService`
  - API 监听 `127.0.0.1:28081`

### Exporter

- `xray-exporter-xhttp.service`
  - 监听 `127.0.0.1:8080`
  - 上游指向 `127.0.0.1:28080`

- `xray-exporter-tcp.service`
  - 监听 `127.0.0.1:8081`
  - 上游指向 `127.0.0.1:28081`

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

- [`roles/vhosts/agent-proxy/defaults/main.yml`](/Users/shenlan/workspaces/ai-workspace-infra/playbooks/roles/vhosts/agent-proxy/defaults/main.yml)
- [`roles/vhosts/agent-proxy/templates/xray.service.j2`](/Users/shenlan/workspaces/ai-workspace-infra/playbooks/roles/vhosts/agent-proxy/templates/xray.service.j2)
- [`roles/vhosts/agent-proxy/templates/xray-tcp.service.j2`](/Users/shenlan/workspaces/ai-workspace-infra/playbooks/roles/vhosts/agent-proxy/templates/xray-tcp.service.j2)
- [`roles/vhosts/agent-proxy/templates/xray.xhttp.template.json.j2`](/Users/shenlan/workspaces/ai-workspace-infra/playbooks/roles/vhosts/agent-proxy/templates/xray.xhttp.template.json.j2)
- [`roles/vhosts/agent-proxy/templates/xray.tcp.template.json.j2`](/Users/shenlan/workspaces/ai-workspace-infra/playbooks/roles/vhosts/agent-proxy/templates/xray.tcp.template.json.j2)
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
1. **远端 Xray 节点统一使用 28080/28081**：
   - `tky-proxy.svc.plus` 的 XHTTP/TCP Xray Stats API 分别监听 `127.0.0.1:28080` 和 `127.0.0.1:28081`。
   - 两个 exporter 分别监听 `127.0.0.1:8080`、`127.0.0.1:8081`，并访问对应的 28080/28081 上游。
2. **observability 主机不启动本地 Xray**：
   - `install.svc.plus` 仅运行 exporter、Vector/VictoriaMetrics 等观测组件，不把 exporter 绑定到本机失效的 `xray.service` / `xray-tcp.service`。
   - 通过 `xray_exporter_require_local_xray_services: false` 清除 systemd 本地 Xray 依赖，避免本地容器端口或证书残留导致 exporter 被 systemd 停止。

### 证书路径修正
- `xray-tcp.service` 在该主机上加载的 TLS 证书路径修正为本地实际存在的 `xworkmate-bridge.svc.plus` 证书目录（`/var/lib/caddy/.local/share/caddy/certificates/.../xworkmate-bridge.svc.plus/`）。

### 调整后在该主机的端口拓扑
- `tky-proxy.svc.plus` 的 `xray.service` (XHTTP API)：`127.0.0.1:28080`
- `tky-proxy.svc.plus` 的 `xray-exporter-xhttp.service`：监听 `127.0.0.1:8080`
- `tky-proxy.svc.plus` 的 `xray-tcp.service` (TCP API)：`127.0.0.1:28081`
- `tky-proxy.svc.plus` 的 `xray-exporter-tcp.service`：监听 `127.0.0.1:8081`
- `vector.service` (指标抓取)：继续通过 `127.0.0.1:8080/scrape` 和 `127.0.0.1:8081/scrape` 正常抓取。
