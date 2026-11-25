# HAProxy 3.0 零中断平滑切流至 Fluxgate（jp-xconnect-01 -> jp-xconnect-02）

- 日期：2026-11-25
- 源节点：`jp-xconnect-01.svc.plus`（`3.113.5.51`，HAProxy 3.0.11 + Xray）
- 目标节点：`jp-xconnect-02.svc.plus`（`43.207.89.150`，Fluxgate + 内嵌 Xray `26.10.10`）
- 关联组件：`roles/vhosts/haproxy`、`be_xray_xhttp`、`/run/haproxy/admin.sock`、Fluxgate v0.3.0 Swarm Grouping Mode

## 背景与目标

`jp-xconnect-01.svc.plus` 承载大量长连接 XHTTP/H2 用户会话，需要将其线上流量平滑切流至新节点 `jp-xconnect-02.svc.plus`。

如果直接修改 DNS 权重或重启代理进程，会遇到两个问题：

1. 客户端与递归解析器存在 DNS 缓存，无法在秒级控制新建连接比例，也无法按 `80/20 -> 50/50 -> 20/80 -> 0/100` 渐进放量观察稳定性。
2. 直接执行 `systemctl restart haproxy` 会切断正在传输中的长连接（XHTTP/H2 tunnel）。

因此采用 **HAProxy 双后端 + `reload`（基于 `expose-fd listeners`）+ Admin Socket 热调权** 方案：在 `jp-xconnect-01` 的 `be_xray_xhttp` 中同时挂接本地 Unix Socket（`/dev/shm/xray.sock`）与远端 `jp-xconnect-02.svc.plus`（`43.207.89.150:443`，H2 TLS 透传），通过一次无损 `reload` 加载双后端后，全程通过 `/run/haproxy/admin.sock` 动态调整权重实现零中断切流。

> [!IMPORTANT]
> **非首次初始化严禁 `restart`**：HAProxy `global` 段已启用 `stats socket /run/haproxy/admin.sock mode 660 level admin expose-fd listeners`。所有后续配置变更（包括 Ansible role 触发与人工应急修改）必须使用 `reload`（`systemctl reload haproxy` / handler `Reload haproxy` `state: reloaded`），严禁使用 `systemctl restart haproxy`，以保证存量连接由旧 worker 平滑接管直至自然结束。

## 应急操作步骤

### 1. 备份当前配置

```sh
cp /etc/haproxy/haproxy.cfg /etc/haproxy/haproxy.cfg.bak.$(date +%F_%H%M%S)
```

### 2. 配置 `be_xray_xhttp` 双后端

在 `/etc/haproxy/haproxy.cfg` 中更新 `backend be_xray_xhttp`，启用 `balance roundrobin`，保留本地 `/dev/shm/xray.sock`（初始权重 `80`），并新增远端 `jp02`（初始权重 `20`）：

```haproxy
backend be_xray_xhttp
    mode http
    balance roundrobin
    option http-no-delay
    http-request set-path /split/ if { path /split }
    http-request set-query "%[query,regsub('(^|&)x_padding=[^&]*','',g),regsub('^&','')]" if { query -m sub x_padding= }
    server xray /dev/shm/xray.sock proto h2 weight 80
    server jp02 43.207.89.150:443 ssl verify none alpn h2 sni str(jp-xconnect-02.svc.plus) check inter 5s fall 3 rise 2 weight 20
```

### 3. 校验并执行一次性无损 Reload

```sh
haproxy -c -f /etc/haproxy/haproxy.cfg && systemctl reload haproxy
```

- 该步骤依赖 `global` 中的 `expose-fd listeners` 保持既有监听 socket 与存量长连接不中断。
- **操作红线**：除首次引导安装外，任何配置变更都必须使用 `reload`，禁止 `restart`。

### 4. 通过 Admin Socket 渐进热调权重（`80/20 -> 50/50 -> 20/80 -> 0/100`）

无需再次修改配置文件或 reload，直接通过 `/run/haproxy/admin.sock` 实时调整两个 server 的权重：

```sh
# 阶段 1：50/50
echo "set server be_xray_xhttp/xray weight 50" | socat stdio /run/haproxy/admin.sock
echo "set server be_xray_xhttp/jp02 weight 50" | socat stdio /run/haproxy/admin.sock

# 阶段 2：20/80
echo "set server be_xray_xhttp/xray weight 20" | socat stdio /run/haproxy/admin.sock
echo "set server be_xray_xhttp/jp02 weight 80" | socat stdio /run/haproxy/admin.sock

# 阶段 3：0/100（新流量全部切至 jp02，本地存量长连接继续自然排空）
echo "set server be_xray_xhttp/xray weight 0" | socat stdio /run/haproxy/admin.sock
echo "set server be_xray_xhttp/jp02 weight 100" | socat stdio /run/haproxy/admin.sock
```

### 5. 状态与权重验证

```sh
echo "show stat" | socat stdio /run/haproxy/admin.sock | grep be_xray_xhttp | cut -d, -f1,2,5,18,19
```

`20/80` 阶段实测健康输出示例（字段依次为 `pxname,svname,scur,status,weight`）：

```text
be_xray_xhttp,xray,0,no check,20
be_xray_xhttp,jp02,0,UP,80
be_xray_xhttp,BACKEND,0,UP,100
```

确认 `jp02` 状态为 `UP` 且权重按预期生效后，再推进下一档权重。

### 6. 回滚步骤

如观察到 `jp02` 错误率上升、延迟异常或健康检查失败，可先通过 Admin Socket 秒级将权重拉回 `100/0`，再恢复备份配置并执行无损 `reload`：

```sh
# 秒级切回本地（不中断存量连接）
echo "set server be_xray_xhttp/xray weight 100" | socat stdio /run/haproxy/admin.sock
echo "set server be_xray_xhttp/jp02 weight 0" | socat stdio /run/haproxy/admin.sock

# 恢复配置文件并无损 reload（同样禁止 restart）
cp /etc/haproxy/haproxy.cfg.bak.<TIMESTAMP> /etc/haproxy/haproxy.cfg
haproxy -c -f /etc/haproxy/haproxy.cfg && systemctl reload haproxy
```

## Ansible 声明式配置（`roles/vhosts/haproxy`）

应急验证稳定后，可通过 `roles/vhosts/haproxy` 的变量声明固化双后端与权重，避免下次 Ansible 执行覆盖手工配置：

```yaml
haproxy_xhttp_local_weight: 80
haproxy_xhttp_spillover_servers:
  - name: jp02
    address: "43.207.89.150:443"
    ssl: true
    verify: none
    sni: "jp-xconnect-02.svc.plus"
    check_inter: "5s"
    fall: 3
    rise: 2
    weight: 20
```

- 默认 `haproxy_xhttp_spillover_servers: []`、`haproxy_xhttp_local_weight: 100` 时，模板仅渲染单后端本地 `server xray`，保持原有行为不变。
- `roles/vhosts/haproxy/tasks/configure.yml` 所有配置模板变更均 `notify: Reload haproxy`（`state: reloaded`），不会触发 `Restart haproxy`。

## 与 Fluxgate v0.3.0 Swarm Grouping Mode 的协同

本次 HAProxy 应急切流机制与 Fluxgate v0.3.0 Swarm Grouping Mode（参见 `Swarm-Grouping-Mode.conf`）在设计上处于同一分层控制体系的不同阶段：

1. **Local-First + 1-Hop H2 TLS 跨节点溢出**：
   - 本地节点优先由本机进程承接（HAProxy 中为 `/dev/shm/xray.sock`，Fluxgate Swarm 中为 Local-First 策略）；当节点进入排空或高负载状态时，仅经过 **1-Hop H2 TLS** 转发至同组 Peer（如 `jp-xconnect-02.svc.plus`），不依赖客户端 DNS 立即刷新。
2. **标准排空与 GTM 权重联动**：
   - 长期架构下，节点排空通过 `fluxgate swarm drain` 与 Vault KV 中的 GTM 权重协同推进：先通过 1-Hop H2 承接存量与过渡期新建流量，再同步下调 Vault KV GTM 权重逐步收敛 DNS 入口流量。
3. **统一观测闭环**：
   - **数据面指标**：采集 HAProxy 内置 Prometheus exporter（`127.0.0.1:8404/metrics`）中的后端活跃会话数、后端健康状态及响应码，汇聚至 Grafana 面板比对 `xray` 与 `jp02` 流量曲线。
   - **控制面视图**：结合控制台 `/panel/ops/traffic-dispatch` 观察节点组权重分布与排空进度，形成「权重调整 -> `:8404/metrics` / Grafana 观测 -> `/panel/ops/traffic-dispatch` 确认 -> 下一档切流」的完整闭环。
