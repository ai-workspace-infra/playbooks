# 平台导航与总览 (Navigation Homepage) 设计说明

对应文件:[files/homepage-navigation.json](../files/homepage-navigation.json)
线上地址:`https://observability.svc.plus/grafana/d/homepage-navigation/`

## 设计依据

视觉语言参考 [Pigsty demo homepage](https://demo.pigsty.io/ui/d/pigsty/pigsty)(彩色背景磁贴 + 大号实例网格 + 右侧仪表盘目录 + 顶部 tag 折叠导航);信息架构按排障路径整理为六类:

```
环境  →  指标  →  日志  →  链路  →  告警  →  资源
```

首页保留原有 Grafana 顶部按钮样式和总览区,六个入口折叠展示,点击后进入对应的全部面板二级菜单,不改变现有面板布局。

## 数据基准:全部查询已对线上 VictoriaMetrics 实测

**重要前提**:本平台的指标并非来自 Prometheus scrape,而是边缘 Vector agent 经 `/ingest/metrics/` **remote-write 推送**。实测 `up{}` 只有 2 条序列且均为 0(见"已知缺口"),因此**首页所有面板都不依赖 `up` 指标**。

实测确认的活跃数据源(3 台主机,数据新鲜度 2–11 秒):

| 主机 | node | vector | process | xray |
| --- | --- | --- | --- | --- |
| `console-uat.onwalk.net` | ✅ | ✅ | ✅ 37 进程组 | — |
| `install.svc.plus` | ✅ | ✅ | ✅ 148 进程组 | ✅ tcp/xhttp |
| `tky-proxy.svc.plus` | ✅ | ✅ | ✅ 59 进程组 | ✅ tcp/xhttp |

另有 `blackbox` job 提供 5 个 HTTPS 探测目标(accounts / console / jp-xhttp / tky-proxy / www.svc.plus)。

> ⚠ **标签陷阱**:Vector agent 把 **所有** 推送的指标(node / vector / namedprocess / xray)统一打上了 `job="xray"`。这是 agent 侧的标签配置问题。因此首页查询一律按 `instance` 聚合,**绝不能按 `job` 过滤 node 指标**,否则语义完全错误。

## 顶部导航

Pigsty 风格的下拉导航。**每个下拉的 tag 都已在对应 dashboard JSON 中实际标注并验证非空** —— 直接照搬 Pigsty 的 `PGSQL / NODE / INFRA / Module` 会全部落空,因为本平台并没有那些仪表盘。

| 入口 | 类型 | 过滤 / 目标 | 实际命中 |
| --- | --- | --- | --- |
| 环境 | 折叠 | tag `ENVIRONMENT` | k6 多环境压测看板 |
| 资源 | 折叠 | tag `RESOURCE` | Node、process、PostgreSQL、Blackbox |
| 指标 | 折叠 | tag `METRICS` | Node、process、PostgreSQL、Xray、k6、Blackbox |
| 日志 | 折叠 | tag `LOGS` | VictoriaLogs Overview |
| 链路 | 折叠 | tag `TRACES` | VictoriaTraces APM、k6 端到端压测 |
| 告警 | 直链 | `/grafana/alerting/list` | Grafana 内置统一告警 |

为此给已部署的仪表盘补了导航 tag:资源类面板使用 `RESOURCE`,指标类面板使用 `METRICS`,k6 使用 `ENVIRONMENT`/`TRACES`,日志与链路面板分别使用 `LOGS`/`TRACES`,并保留原有业务与采集 tag。首页删除顶部导航按钮,只显示 `Environment`、`Metrics`、`Logs`、`Traces`、`告警`、`Resource` 六个变量选择器。

### 首页变量选择器

Grafana 原生变量直接显示在首页顶部:

| 选择器 | 类型 | 默认值 | 用途 |
| --- | --- | --- | --- |
| Environment | 自定义 | All | 传递 SIT / UAT / PROD 压测上下文 |
| Resource | 查询 | All | 选择目标主机实例 |
| Metrics DS | 数据源 | VictoriaMetrics | 首页与指标看板使用的 Prometheus 数据源 |
| Logs DS | 数据源 | VictoriaLogs | k6 / 日志看板使用的 VictoriaLogs 数据源 |
| Traces DS | 数据源 | VictoriaTraces | k6 / APM 看板使用的 Jaeger 数据源 |
| 告警 | 自定义 | All | 告警状态筛选上下文 |

## 面板结构

| 区块 | 面板 | 数据来源 | 实测 |
| --- | --- | --- | --- |
| — | 总览导航 | 静态 HTML,六类排障路径示意 | — |
| 平台脉搏 | 快速入口 | 纯导航磁贴 | — |
| 平台脉搏 | 采集器 | 各 exporter 覆盖主机数,按 instance 去重 | 5 项均有值 |
| 平台脉搏 | 边缘节点 | 每主机 CPU%,area sparkline,可下钻主机详情 | 3 series |
| 平台脉搏 | 仪表盘 | dashlist,不做 tag 过滤 | — |
| 01 COLLECT | CPU / 内存 使用率 | `node_cpu_seconds_total` / `node_memory_*` | 各 3 series |
| 01 COLLECT | 根分区磁盘 | `node_filesystem_*{mountpoint="/"}` | 3 series |
| 01 COLLECT | xray 探针 | `xray_up`,按 transport,0/1 映射 DOWN/UP | 4 series |
| 02 INGEST | Vector 出口事件速率 | `vector_component_sent_events_total` | 3 series |
| 02 INGEST | Vector 缓冲积压 | `vector_buffer_byte_size` | 4 series |
| 02 INGEST | Vector 错误率 | `vector_component_errors_total` | 1 series |
| 03 STORE | 存储引擎与网关 | 纯导航磁贴 | — |
| 03 STORE | SSL 证书剩余 | `probe_ssl_earliest_cert_expiry` | 5 series |
| 03 STORE | Firing Alerts | Grafana 原生 `alertlist` 面板 | 见下 |
| 04 AI DIAGNOSE | MCP 服务入口 | 纯导航磁贴,4 个 MCP 适配器 | — |
| 04 AI DIAGNOSE | MCP 能力说明 | 静态 HTML,含安全边界说明 | — |

所有 PromQL 查询均已逐条打到线上 VictoriaMetrics 验证返回非空。

### 设计取舍

- **纯导航磁贴明确标注**:快速入口 / 存储引擎 / MCP 三个面板用 `expr: 1` 常亮,panel description 里写明"不代表健康状态"。避免用户误以为绿色 = 健康 —— 这几个组件目前确实没有被纳入采集。
- **统一选择器接上了**:首页面板使用 `${DS_METRICS}`,并提供与 k6 / VictoriaTraces 看板一致的 `Environment`、`Metrics DS`、`Logs DS`、`Traces DS` 与 `Resource` 选择器;分类入口会带上当前变量。
- **删除 `interval` 变量**:同样是无人引用的死配置。

## 告警:改用 Grafana 内置统一告警

原方案是 vmalert + Alertmanager 两个容器,但实测 **vmalert 加载了 0 个规则组** —— 本 role 既未创建 `vmalert/` `alertmanager/` 目录也未生成配置文件,却以 `--rule=/etc/vmalert/alerts.yml` 启动容器,告警链路从来就没通过。

现改为 **Grafana 内置统一告警**(Grafana 自带 Alertmanager),规则由 Ansible 配置化下发,不再需要那两个容器:

- 规则模板:[templates/grafana-provisioning-alerting.yml.j2](../templates/grafana-provisioning-alerting.yml.j2) → 落到 `grafana/provisioning/alerting/rules.yml`
- 首页 `Firing Alerts` 面板改用 Grafana 原生 `alertlist` 类型(原来查 `ALERTS{}` 是 vmalert 的概念,Grafana 内置告警不会往数据源写这个序列)
- `vmalert` / `alertmanager` 容器与对应的 Caddy 路由 `/vmalert/` `/alertmgr/` 均由 `observability_vmalert_enabled`(默认 `false`)门控,已从栈中移除,服务数 12 → 10

### 已下发的 9 条规则

| 组 | 规则 | 条件 | for | 级别 |
| --- | --- | --- | --- | --- |
| edge-nodes | 主机 CPU 使用率过高 | > 85% | 5m | warning |
| edge-nodes | 主机内存使用率过高 | > 90% | 5m | warning |
| edge-nodes | 根分区磁盘使用率过高 | > 85% | 10m | warning |
| edge-nodes | 边缘 Agent 失联 | 无上报 > 300s | 5m | critical |
| edge-nodes | xray 探针 DOWN | `xray_up < 1` | 5m | critical |
| ingest-pipeline | Vector 缓冲区积压 | > 0 bytes | 10m | warning |
| ingest-pipeline | Vector 组件错误 | 错误率 > 0 | 5m | warning |
| endpoints | 站点探测失败 | `probe_success < 1` | 5m | critical |
| endpoints | SSL 证书即将过期 | < 14 天 | 1h | warning |

阈值全部通过 `defaults/main.yml` 中的 `observability_alerting_*` 变量配置。

### 通知外发

默认 `observability_alerting_webhook_url` 为空 → **不下发 contactPoints / policies,仅评估并在 Grafana 内展示告警状态,不外发通知**。配置该变量后会自动生成 webhook 联络点与通知策略。

### 两个部署侧的坑(已修)

1. **Grafana 的 alerting provisioning 只在启动时加载**,而 `docker compose up -d` 不会重建配置未变的 grafana 容器。新增 `Restart grafana` handler,告警规则变更时显式 `docker compose restart grafana`。
2. **建目录任务原本没有打 tag**,导致 `--tags observability` 运行时整个被跳过,新增的 `provisioning/alerting` 目录建不出来、模板下发直接失败。已给该任务补上 tags。

## 已知缺口(预览骨架已留位,待后续接入)

1. **scrape 侧基本全灭** —— `up{}` 仅存 2 条且均为 0:
   - `job="prometheus"` 抓 `localhost:9090`,但 VictoriaMetrics 容器内实际监听 **8428**(9090 只是宿主机侧端口映射),配置错误;
   - `job="aws-costs"` 抓 `opencost-exporter-aws:9100`,同样 DOWN。
   - 模板中声明的 `gcp-costs` / `azure-costs` / `mcp-*` 四个 job 在线上**完全不存在**(label values 里查不到),说明 VictoriaMetrics 未重载 promscrape 配置,或这些 target 从未成功注册。
2. **GCP / Azure 成本采集器仍是占位容器** —— `command: tail -f /dev/null`,注释写着 "Placeholder pending specific exporter image"。
3. **MCP / Victoria 三兄弟自监控指标未接入** —— 上线后可把对应的纯导航磁贴升级为带健康状态。
4. **Vector agent 标签污染** —— 所有推送指标被统一打上 `job="xray"`,建议在 agent 侧 VRL 里按来源正确赋值 `job`,否则后续任何按 job 的聚合都会出错。
