# 平台导航首页(homepage-navigation)优化 Spec

对应文件:[files/homepage-navigation.json](../files/homepage-navigation.json)
基准:`observability.svc.plus/grafana` 线上实际运行的 Docker Compose 栈(`playbooks/roles/docker/observability-server`),不是 Pigsty 模板。

## 背景:之前的方向走偏了

首页最初的结构(IaaS→PaaS→SaaS、计算/存储/网络、控制面/集群/DB/缓存、代理/请求)是从 Pigsty 的仪表盘模板搬过来的,但线上这套栈实际只是一个轻量 Docker Compose 栈:VictoriaMetrics / VictoriaLogs / VictoriaTraces + Grafana + vmalert/Alertmanager + FinOps 成本采集器 + 面向 AI Agent 的 MCP Server(见 [README.md](../README.md))——没有 PGSQL/NODE/REDIS/MINIO 这类服务舰队,也没有对应的分类仪表盘。按 Pigsty 结构做的 tag 过滤 dashlist 会一直是空的。这一版把首页结构改成贴合这套栈实际有什么。

## 现在实际有什么(全部来自本 role 自己的模板文件,不是猜的)

- **容器**(`templates/docker-compose.yml.j2`):victoria-metrics、victoria-logs、victoria-traces、vmalert、alertmanager、blackbox-exporter、grafana、opencost-exporter-aws(真的在采集)、opencost-exporter-gcp / opencost-exporter-azure(占位容器,`command: tail -f /dev/null`,注释写着"Placeholder pending specific exporter image")、mcp-grafana / mcp-victoriametrics(默认开启)、mcp-victorialogs / mcp-victoriatraces(默认关闭)。
- **Prometheus scrape job**(`templates/prometheus.yml.j2`):`prometheus`(自监控)、`aws-costs`、`gcp-costs`、`azure-costs`、`mcp-grafana`、`mcp-victoriametrics`、`mcp-victorialogs`、`mcp-victoriatraces`。**没有** `pgsql`/`redis`/`node`/`nginx`/`minio` 之类的 job。
- **对外路径**(`templates/observability.caddy.j2`):`/grafana/`、`/vmetrics/`、`/vlogs/`、`/vtraces/`、`/vmalert/`、`/alertmgr/`、`/blackbox/`、`/mcp/grafana/`、`/mcp/victoriametrics/`(其余两个 MCP 路径视开关而定)。
- **已经在跑的仪表盘**(`files/` 目录,只有这 3 个 + 首页自己):`dashboard.json`(标题 "Xray Dashboard",无 tag)、`Node-Exporter-Dashboard.json`(通用主机监控,社区模板)、`process-exporter-dashboard-with-treemap.json`(进程监控 treemap)。没有任何分类 tag 体系。

## 变更清单

1. **顶部导航链接**:原来的 PGSQL/NODE/INFRA/Module 下拉过滤的 tag,在线上这 3 个真实仪表盘里一个都不存在,下拉出来全是空的。改成一个 `全部仪表盘` 下拉(`tags: []`),始终和实际已上传的仪表盘同步,不需要维护 tag 映射。
2. **首页 Hero 区文案**:去掉"IaaS→PaaS→SaaS / 计算·存储·网络"这类 Pigsty 措辞,改成描述这套栈实际在做的事(VictoriaMetrics/Logs/Traces + Grafana,给 AI Agent 提供只读查询接口),3 个胶囊换成:观测数据 / 告警与拨测 / MCP 接入。
3. **"平台脉搏"三个面板换成查真实 job**(原来的 `Open Platform OBS v1.0`/`Modules`/`Instances` 三个面板全部删除,因为它们查的 `up{job="pgsql"}` 等在线上没有对应采集目标,大概率一直空着):
   - **MCP Server 状态**:`sum(up{job="mcp-grafana"})` / `mcp-victoriametrics` / `mcp-victorialogs` / `mcp-victoriatraces`,链接到对应 `/mcp/<name>/` 路径。VictoriaLogs / VictoriaTraces 的 MCP 默认关闭,对应方块默认 No Data 是预期行为。
   - **成本采集 & 自监控**:`sum(up{job="prometheus"})`(自监控)+ `aws-costs`/`gcp-costs`/`azure-costs`。GCP / Azure 的采集器是占位容器,会一直 Down,这是已知状态不是故障(见下面"发现但本次未动")。
   - **Firing Alerts**:保留不动。它查询的 `ALERTS{alertstate="firing"}` 是 vmalert 通过 `--remoteWrite.url` 主动推送进 VictoriaMetrics 的,不依赖 scrape job,链路本身是通的,不受这次改动影响。
4. **"IaaS资源/PaaS服务/业务监控"三个大区块整体删除**,换成一个"组件与仪表盘"区块:
   - **组件入口**:VictoriaMetrics / VictoriaLogs / VictoriaTraces / vmalert / Alertmanager / Blackbox Exporter 6 个纯导航方块,直接链到各自的 `/vmetrics/`、`/vlogs/`、`/vtraces/`、`/vmalert/`、`/alertmgr/`、`/blackbox/` 路径。这几个组件目前没有各自独立的 `up` 指标,方块常亮不代表健康,面板 description 里已注明。
   - **全部仪表盘**:一个不带 tag 过滤的 dashlist,把 `dashboard.json`/`Node-Exporter-Dashboard.json`/`process-exporter-dashboard-with-treemap.json` 都列出来。现在只有 3 个仪表盘,不分类反而更诚实;以后仪表盘多起来、真的需要分区时再加 tag 过滤。
5. **删除未被任何查询引用的模板变量 `interval`**(死配置)。
6. 首页总高度从原来的 62 行降到 21 行。

## 发现但本次未动(记录一下,不属于"面板优化"范围,需要你决定要不要修)

- `prometheus.yml.j2` 里 `job_name: prometheus` 的采集目标是 `localhost:9090`,但 VictoriaMetrics 容器本身监听的是 8428(datasource 配置和 docker-compose 端口映射都是 8428);9090 只是宿主机侧的端口映射,容器内部并不监听这个端口。这个自监控 job 大概率一直抓取失败,"成本采集 & 自监控"面板里的"自监控"方块目前会显示 Down——这不是这次改动引入的,是已有配置里的问题,要不要顺手修可以再定。
- `opencost-exporter-gcp` / `opencost-exporter-azure` 是占位容器,还没接真实的 GCP/Azure 成本采集逻辑,对应方块会一直 Down,是已知未完成状态。

## 依据来源

本次所有改动依据均来自这个 role 自己的模板文件,不再参考 `ai-workspace-infra/observability/`(那是另一套没有实际部署的 Pigsty fork,和线上跑的不是一回事):

- 容器清单 → `templates/docker-compose.yml.j2`
- 真实 scrape job → `templates/prometheus.yml.j2`
- 对外路径 → `templates/observability.caddy.j2`
- 已部署仪表盘及其 tag → `files/dashboard.json` / `files/Node-Exporter-Dashboard.json` / `files/process-exporter-dashboard-with-treemap.json`
