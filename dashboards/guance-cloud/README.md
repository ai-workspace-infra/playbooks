# 观测云（Guance Cloud）仪表板存储目录

本目录专门存储迁移至 **观测云控制台（Guance Cloud Console）** 的标准仪表板规范定义文件（JSON 格式），已完成与 `ev-k3s-v6` / `dev-k3s-v6` 环境及中央监控体系的无缝对接。

---

## 一、仪表板清单

| 仪表板名称 | 标准定义文件 | 图表数 | 视图变量 | 核心监控指标 |
| :--- | :--- | :---: | :--- | :--- |
| **Caddy 网关监控** | [`caddy-dashboard.json`](./caddy-dashboard.json) | 25 | `#{environment}`, `#{cluster}`, `#{service}`, `#{instance}`, `#{interval}` | 重载状态、HTTP 请求速率、请求/响应大小中位数、响应耗时分布、4xx/5xx 错误占比、Upstream 实例健康状态 |
| **PostgreSQL 统一监控概览** | [`postgres-unified-overview.json`](./postgres-unified-overview.json) | 14 | `#{environment}`, `#{cluster}`, `#{service}`, `#{instance}`, `#{datname}` | `pg_up` 可用性、活跃连接数、事务提交与回滚率、缓存命中率、数据库存储容量、死锁与冲突数、BGWriter 刷盘性能 |
| **K8S 集群监控 StarsL.cn** | [`starsl-k8s-dashboard.json`](./starsl-k8s-dashboard.json) | 33 | `#{cluster}`, `#{environment}`, `#{Node}`, `#{NameSpace}`, `#{Container}`, `#{Pod}` | 节点状态与资源水位（CPU/内存/磁盘）、Pod 状态与容器配额（Request/Limit）、命名空间限额、PVC 容量与 IO、网络吞吐 |

---

## 二、观测云 PromQL 与模板变量规范

1. **模板变量占位符**：
   - 采用观测云标准 `#{variable_code}` 语法，完全替代 Grafana 的 `${variable:regex}` 格式。
   - 分组标题与展开状态支持动态渲染（如 `节点资源总览：所选节点:【#{Node}】`）。
2. **通配符正则保护 (`starMeaning`)**：
   - 所有支持全选（`includeStar: true`）的 PromQL 变量，其 `extend.starMeaning` 均明确配置为 `".*"`。
   - 避免 PromQL 正则匹配出现 `{cluster=~"*"}`（报 `missing argument to repetition operator: *` 语法错误）。
3. **Grafana 特有时间宏替换**：
   - 替换 Grafana 独有的 `$__range` 与 `$__rate_interval` 宏。
   - Caddy 仪表板统一绑定为视图变量 `[#{interval}]`，PostgreSQL 与 K8S 仪表板统一采用标准稳态步长 `[5m]`。
4. **指标名保留机制**：
   - DataKit 采集端通过 `keep_exist_metric_name = true` 保持原生 Prometheus 指标命名（`caddy_*`、`pg_*`、`kube_*`、`node_*`、`container_*`），与 Grafana 口径 100% 保持一致。

---

## 三、控制台导入与更新指南

1. 打开 [观测云控制台](https://cn3-console.guance.com/)，进入对应工作空间（如 `ai-workspace`）。
2. 在左侧菜单栏选择 **场景 (Scenes)** -> **仪表板 (Dashboards)**。
3. 点击右上角 **新建仪表板** -> **导入仪表板 (Import Dashboard)**。
4. 选择对应的 JSON 文件（`caddy-dashboard.json`、`starsl-k8s-dashboard.json` 或 `postgres-unified-overview.json`）完成导入。
5. 如需覆盖现有仪表板，可在对应仪表板页面右上角点击设置 -> 导入更新并上传最新 JSON。
