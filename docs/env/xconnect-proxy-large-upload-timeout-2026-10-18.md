# XConnect Proxy 大文件上传超时排查记录（2026-10-18）

## 范围

检查并对齐以下三个 Selfhost PROD 节点的 Xray 连接策略：

- `jp-free-selfhost-prod-v2.svc.plus`
- `sg-free-selfhost-prod-v2.svc.plus`
- `us-free-selfhost-prod-v2.svc.plus`

GitOps 当前声明拓扑和入口；本次是主机运行参数，由 Playbooks 的 `agent-proxy` Role 负责，因此不修改 GitOps 拓扑。

## 链路与现场证据

请求路径为客户端 → 节点 Caddy HTTPS → `/split` XHTTP handler → Unix socket `/dev/shm/xray.sock` → Xray → 目标 Web 服务。Caddy 通过 h2c 转发到 Xray；三个目标域名的在线路由未显式设置 `flush_interval`，沿用默认刷新策略。在线配置未发现请求体大小限制或 Caddy 读写超时设置。

三个节点运行 Xray `26.3.27`，XHTTP inbound 为 `mode: auto`、`path: /split`。验证时 `xray`、`xray-tcp`、Caddy 均 active；Xray 两份配置可通过 `xray run -test`，三个 HTTPS 入口均返回 200。

JP 最近 48 小时的 Caddy 记录中，`/split` POST 请求声明体约 0.5–1 MB，部分请求持续约 98–1100 秒后以 502 结束，错误为 `unexpected EOF` 或上游 Unix socket `connection reset by peer`。还观察到短暂的 Xray socket 不存在错误。SG 未见同窗口内的同类长请求 502；US 有少量 socket 不存在错误，但没有相同的长上传记录。网卡累计 RX/TX error 和 dropped 均为 0。

证据排除了 Caddy 配置中的固定文件大小上限，并显示失败发生在 XHTTP 分片/上游连接链路；目前不足以判断是客户端所选 XHTTP 模式、客户端网络中断，还是 Xray 上游会话重置。历史日志未提供原始目标站点的响应和客户端失败记录；本次补做受控 HTTPS 端到端实测，结果见验收 case。

## 配置调整

三个在线节点的 XHTTP 与 TCP Xray 配置及模板均已对齐为 `policy.levels.0.connIdle: 3600` 秒，并完成配置校验和服务状态检查。Playbooks 现将 `xray_connection_idle_timeout_seconds` 默认值从 1800 提升至 3600；XHTTP 与 TCP 模板共用此变量，后续角色部署可保持一致。

该值只延长连接**无上下行数据时**允许保持的时间，不限制上传总时长，也不增加文件大小上限。该 Role 默认值适用于所有未单独覆盖该变量的 managed agent-proxy 主机。要确认大文件体验，仍需在客户端侧复现，并同时记录所选 XHTTP mode、客户端/Core 版本、目标站点、失败时间、请求 ID 和对应 Caddy/Xray 日志。

## 验证记录

- 三节点：Xray 配置测试通过；`xray`、`xray-tcp`、Caddy 为 active。
- 三节点：HTTPS 根路径返回 HTTP 200。
- JP：已从 Caddy 日志定位历史长 POST 502；不能把首页 200 当作上传链路验收。
- 真实文件 1–512 MB、两种模式、三节点共 60 项的结果与原始证据见 [验收 case](../cases/xconnect-free-proxy-upload-acceptance-2026-10-18.md)。受控接收端结果不代表 ChatGPT 页面上传验收。

## Agent 缓存回写与持久修复

第一次远程实测运行 `smoke-20261018T040909Z-92a47842`，执行 58 项、53 项通过、5 项失败后停止。北京时间 12:16:01–12:16:06，各节点 Agent 在 Accounts 配置同步后重启 Xray 服务，Caddy 进程及受控接收路由保持不变；失败表现包括 HTTP/2 流提前关闭、TLS EOF 和上传中断。随后检查到运行配置的 connIdle 回退（XHTTP 未显式设置，TCP 为 120），而磁盘模板仍为 3600。

部署 Agent revision `935f539a0caa93a094eb0562a5fa8d96d19ee54b` 在启动时读取并缓存模板。仅更新磁盘模板不重载 Agent，会使后续配置同步继续使用旧模板。这解释了参数回退；日志不足以判定该次同步的具体用户差异，不能把所有历史 EOF 都归为同一原因。

已在三个节点重载 `xconnect-edge-agent`，由其重新生成并验证运行配置；两轮后续同步后仍保持 3600。Role 的两个模板任务现通知现有 Agent handler，避免后续部署重复该问题。

## SSH Playbook 应用

- Owner：现有 `roles/vhosts/agent-proxy`，新增 `tasks/upload_timeout.yml` 和针对现有 JSON 的窄范围修改工具。
- Caller：`tune_xray_upload_timeout.yml`；操作人通过临时 inventory 的 `xconnect_upload_targets` 仅选择用户授权的三个节点。临时清单不作为第二份 CMDB/GitOps 拓扑。
- 副作用：只修改现有模板的 `policy.levels.0.connIdle`，保留其他字段和权限，变更前保存 root 私有备份；模板或运行配置漂移时重载既有 Agent。不会重装 Core/Agent、覆盖用户或更改 Caddy。运行配置由 Agent 生成。
- 幂等：模板、运行配置已一致时不重启；运行配置漂移时即使模板内容未变也触发 Agent 重载。
- 现场执行：北京时间 2026-10-18 12:28:09 开始，约 45 秒。JP/SG/US 均 `unreachable=0 failed=0 changed=0`，两份 Xray 配置验证通过，四个服务均 active。参数已由前述修复加载，因此本次实际 Playbook 对齐不再次重启服务。

```sh
ansible-playbook -i "$RUNTIME_SCOPED_INVENTORY" tune_xray_upload_timeout.yml
```

`RUNTIME_SCOPED_INVENTORY` 来自授权目标选择，组名为 `xconnect_upload_targets`；SSH 私钥保持在本机，不能写入仓库。此入口只用于已安装、使用 `xconnect-edge-agent` 服务名的节点。Role 默认值影响未来调用该 Role 的主机；本次现场执行只覆盖上述三个目标。

## 完整实测结论

本轮实际执行 60/60 项，58 成功（96.67%）：`auto` 三节点 30/30，`packet-up` 28/30。JP packet-up 512 MB 在 140.2704 秒、已发送 278,855,380 bytes 后 reset；US packet-up 32 MB 在 83.0699 秒因连续 60 秒低于速率阈值失败，其后 64–512 MB 仍成功。两项失败的根因尚未确定，当前不能宣布所有上传问题解决；未自动重试覆盖失败。

失败时段 journal 未检索到 Xray 服务重启。延迟复查观察到北京时间 13:16 的同步重启，晚于实际上传结束（12:46:17）和清理完成（13:05:31）；其后四份策略仍为 3600，证明缓存修复后未再次发生同样的策略回退。控制端 SSH 采集和清理耗时单独记录，不计入每项 curl 耗时。Caddy 和 Agent PID 保持不变；两项 packet-up 问题保留为待定位项。

全部运行专属客户端、文件、接收进程和临时路由已清理；四个生产服务均 active。完整表格和原始 CSV/JSON 见验收 case。
