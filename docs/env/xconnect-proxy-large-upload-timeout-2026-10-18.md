# XConnect Proxy 大文件上传超时排查记录（2026-10-18）

## 范围

检查并对齐以下三个 Selfhost PROD 节点的 Xray 连接策略：

- `jp-free-selfhost-prod-v2.svc.plus`
- `sg-free-selfhost-prod-v2.svc.plus`
- `us-free-selfhost-prod-v2.svc.plus`

GitOps 当前声明拓扑和入口；本次是主机运行参数，由 Playbooks 的 `agent-proxy` Role 负责，因此不修改 GitOps 拓扑。

## 链路与现场证据

请求路径为客户端 → 节点 Caddy HTTPS → `/split` XHTTP handler → Unix socket `/dev/shm/xray.sock` → Xray → 目标 Web 服务。Caddy 对该路由使用 `flush_interval -1`，并通过 h2c 转发到 Xray；在线配置未发现请求体大小限制或 Caddy 读写超时设置。

三个节点运行 Xray `26.3.27`，XHTTP inbound 为 `mode: auto`、`path: /split`。验证时 `xray`、`xray-tcp`、Caddy 均 active；Xray 两份配置可通过 `xray run -test`，三个 HTTPS 入口均返回 200。

JP 最近 48 小时的 Caddy 记录中，`/split` POST 请求声明体约 0.5–1 MB，部分请求持续约 98–1100 秒后以 502 结束，错误为 `unexpected EOF` 或上游 Unix socket `connection reset by peer`。还观察到短暂的 Xray socket 不存在错误。SG 未见同窗口内的同类长请求 502；US 有少量 socket 不存在错误，但没有相同的长上传记录。网卡累计 RX/TX error 和 dropped 均为 0。

证据排除了 Caddy 配置中的固定文件大小上限，并显示失败发生在 XHTTP 分片/上游连接链路；目前不足以判断是客户端所选 XHTTP 模式、客户端网络中断，还是 Xray 上游会话重置。日志没有提供原始 Web 上传的目标响应和客户端侧失败记录，因此本记录不宣称端到端大文件上传已验收。

## 配置调整

三个在线节点的 XHTTP 与 TCP Xray 配置及模板均已人工对齐为 `policy.levels.0.connIdle: 3600` 秒，并完成配置校验和服务状态检查。Playbooks 现将 `xray_connection_idle_timeout_seconds` 默认值从 1800 提升至 3600；XHTTP 与 TCP 模板共用此变量，后续角色部署可保持一致。

该值只延长连接**无上下行数据时**允许保持的时间，不限制上传总时长，也不增加文件大小上限。该 Role 默认值适用于所有未单独覆盖该变量的 managed agent-proxy 主机。要确认大文件体验，仍需在客户端侧复现，并同时记录所选 XHTTP mode、客户端/Core 版本、目标站点、失败时间、请求 ID 和对应 Caddy/Xray 日志。

## 验证记录

- 三节点：Xray 配置测试通过；`xray`、`xray-tcp`、Caddy 为 active。
- 三节点：HTTPS 根路径返回 HTTP 200。
- JP：已从 Caddy 日志定位历史长 POST 502；不能把首页 200 当作上传链路验收。
- 尚未执行真实 512 MB 上传。本记录用于配置对齐与诊断基线，不代表目标 Web 服务已确认接受该大小文件。
