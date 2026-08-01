# UAT 主机在探测生产端点、并把自己标成生产（2026-08-01）

> 供下午与 Codex 联合调试使用。本文档记录**已确认的事实**、**已提交的改动**、
> 以及**尚未验证/尚未修复的部分**，三者严格分开——没有验证过的地方我会明说。

## TL;DR

UAT 主机 `agent-proxy.onwalk.net`（45.32.19.172）上的 vector agent 正在：

1. 每 60s 对**生产**站点（`accounts.svc.plus` / `console.svc.plus` /
   `www.svc.plus` / `jp-xhttp.svc.plus` / `tky-proxy.svc.plus`）发真实 HTTPS
   探测请求；
2. 把探测结果与本机指标打上 `company=svc.plus`、`project=svc.plus`、
   **`env=production`** 标签，送进共享的 `observability.svc.plus`。

结果是生产看板上混入了一台实际属于 UAT 的机器，**且从标签上无法分辨**。

根因是三处硬编码默认值。已在 playbooks 仓提交参数化修复，生产渲染结果与改动前
**逐字一致**（零回归）。

**注意**：sink 指向 `observability.svc.plus` 是**有意的集中式收集，不是问题**，
本次未改动，环境区分靠标签而非拆分后端。

## 排查起点与一次错误假设

起点是「agent-proxy 有没有错误注册到生产 accounts.svc.plus」。

**结论是没有**，这条链路是干净的：

```
agent-svc-plus (pid 15133) → 167.179.110.129:443 = accounts-uat.onwalk.net  ✅
/etc/agent/account-agent.yaml:  controllerUrl: "https://accounts-uat.onwalk.net"
配置内零处 svc.plus
```

`/opt/agent.svc.plus/` 下确实有多个含 `svc.plus` 的文件，但那是源码 checkout
自带的仓库默认值；systemd 实际加载的是 `/etc/agent/account-agent.yaml`
（`ExecStart=... -config /etc/agent/account-agent.yaml`），那些文件未被使用。

> 值得记下的是：`roles/vhosts/agent-svc-plus/defaults/main.yml` 里
> `agent_controller_url` 的默认值**就是** `https://accounts.svc.plus`（生产），
> 只是被 platform-ops 的 `AGENT_CONTROLLER_URL` 环境变量覆盖掉了。这与本次
> vector 的问题是**同一类**——默认值指向生产，靠上层覆盖兜底。agent 这条侥幸
> 有人覆盖，vector 这条没有。

真正的问题是在 `ss -tnp` 里看出站连接时顺带发现的：

```
ESTAB  45.32.19.172:38852 → 167.179.110.129:443   agent-svc-plus   ← 正确(uat)
ESTAB  45.32.19.172:59040 →  46.250.251.132:443   vector           ← 生产 IP
```

`46.250.251.132` 同时是 `accounts.svc.plus` / `observability.svc.plus` /
`install.svc.plus`（同一台生产机），所以仅凭 IP 无法判断 vector 在跟谁说话，
必须看配置才能区分「往 observability 发数据（合理）」和「探测 accounts（问题）」。

## 三处硬编码

### 1. 探测目标写死为生产域名

`roles/vhosts/blackbox_exporter/defaults/main.yml`：

```yaml
blackbox_ssl_targets:
  - name: "jp-xhttp.svc.plus"     # 生产独有的代理节点
  - name: "tky-proxy.svc.plus"    # 生产独有的代理节点
  - name: "www.svc.plus"
  - name: "console.svc.plus"
  - name: "accounts.svc.plus"
```

vector 模板直接消费它：

```jinja
{% set ssl_targets = blackbox_ssl_targets | default(...) %}
endpoints = ["http://127.0.0.1:9115/probe?module=https_ssl&target={{ target.url | urlencode }}"]
```

### 2 & 3. company/project/env 标签写死

`roles/vhosts/vector-agent/templates/vector.toml.j2`：

```jinja
.tags.company = "svc.plus"
.tags.project = "svc.plus"
.tags.env = "production"     # ← 危害最大的一条
```

`env=production` 写死意味着 UAT 数据在看板上**主动冒充生产**。前两条至少还能靠
`instance` 标签的主机名反推，这一条是直接给了错误答案。

## 已提交的修复

按 `TARGET_DOMAIN_BASE` / `DEPLOY_ENV` 两个环境变量派生（platform-ops 已在传这
两个值），默认值保持生产语义，因此**不传 = 生产 = 与改动前完全一致**。

```yaml
blackbox_probe_domain_base: "{{ lookup('env','TARGET_DOMAIN_BASE') | default('svc.plus', true) }}"
blackbox_probe_env:         "{{ lookup('env','DEPLOY_ENV')         | default('production', true) }}"
blackbox_probe_env_suffix:  "{{ '' if blackbox_probe_domain_base == 'svc.plus' else '-' ~ blackbox_probe_env }}"
```

站点命名在两个环境不同（生产 `console.svc.plus`，非生产 `console-uat.onwalk.net`），
所以后缀单独算。`jp-xhttp` / `tky-proxy` 是生产独有节点，非生产环境没有对应物，
只在 `svc.plus` 时才纳入——否则会去探一堆不存在的主机名。

### 渲染验证（已实测）

```
########## 生产（不设环境变量）##########
env=production base=svc.plus
https://jp-xhttp.svc.plus
https://tky-proxy.svc.plus
https://www.svc.plus
https://console.svc.plus
https://accounts.svc.plus      ← 5 个目标，与改动前逐字一致

########## UAT ##########
env=uat base=onwalk.net
https://console-uat.onwalk.net
https://accounts-uat.onwalk.net ← 只探自己，零生产目标
```

同时确认 sink 的 `endpoint` / `uri` 零改动（`git diff` 过滤后无命中，仅注释文字
提到该域名）。

## 尚未完成 / 需要下午一起看的部分

### A. 改动尚未在真实主机上验证

上面只做了**模板渲染层面**的验证。尚未跑一次真实部署确认：

- 渲染到 `/etc/vector/vector.toml` 的内容符合预期
- vector 重载后确实不再对生产端点发起探测（需在主机上看 `ss` / vector 日志）
- 已经进入 `observability.svc.plus` 的历史脏数据如何处理（是否需要按
  `instance=agent-proxy.onwalk.net` 清理旧序列）

**vector 是否会在配置变更后自动重载，我没有验证过。** 如果它和 Caddy 一样只在
启动时读配置，那这次改动同样需要一个 restart handler——这正是 2026-07-31
web-saas TLS 那次的教训：证书恢复、渲染、挂载全部正确，唯独没人让 Caddy 重新读
一遍配置，于是容器持续跑着 24 分钟前的旧配置，而每一步都显示成功
（修复见本仓 `roles/vhosts/web_saas_host_config/handlers/main.yml`，
完整复盘在 platform-ops-toolkit 仓
`docs/cases/runbook/2026-07-31-web-saas-tls-persistence-deadlock.md`）。
**建议下午优先确认这一点。**

### B. 同类隐患：默认值指向生产

已知至少两处「默认值 = 生产，靠上层覆盖兜底」：

| 位置 | 默认值 | 是否有覆盖 |
|---|---|---|
| `roles/vhosts/agent-svc-plus/defaults/main.yml` `agent_controller_url` | `https://accounts.svc.plus` | 有（platform-ops 传 `AGENT_CONTROLLER_URL`）|
| `roles/vhosts/blackbox_exporter/defaults/main.yml` `blackbox_ssl_targets` | 5 个 `svc.plus` 站点 | **无**（本次修复前）|

建议全仓扫一遍还有多少这种模式。这类默认值的问题在于：**忘记覆盖时不会报错，
只会静默连上生产**。更安全的形态是默认值留空并加断言，强制调用方显式给值。

### C. 环境状态提醒

排查期间 `console-uat.onwalk.net` 的主机 SSH host key 变过多次，`uptime` 显示是
不同的机器——说明期间发生过多轮 destroy/rebuild。若下午调试时连不上或数据对不
上，先确认当前 IP 与主机身份：

```bash
dig +short console-uat.onwalk.net @1.1.1.1
ssh-keygen -R <ip>   # host key 会随重建变化，属正常
```

另有一个**独立的、尚未处理**的问题：`target_domains=all` 与
`target_domains="web-saas + agent-proxy"` 走两个不同的 Terraform workspace，
用错取值做 destroy 会命中空 state 并报「0 destroyed」假绿，而真实资源仍在计费。
详见对话记录，本次未修。

## 复现与验证命令

```bash
# 主机侧：当前实际探测目标与标签
ssh root@45.32.19.172 'grep -oE "target=https%3A//[a-z0-9.-]+" /etc/vector/vector.toml | sort -u'
ssh root@45.32.19.172 'grep -nE "tags.(company|project|env)" /etc/vector/vector.toml'

# 主机侧：vector 实际在跟谁说话
ssh root@45.32.19.172 'ss -tnp | grep vector'

# 本地：两种环境的渲染差异（不连主机）
ansible-playbook /tmp/bb2.yml -e tag=prod                                    # 生产
TARGET_DOMAIN_BASE=onwalk.net DEPLOY_ENV=uat ansible-playbook /tmp/bb2.yml -e tag=uat
```

## 相关文件

| 文件 | 改动 |
|---|---|
| `roles/vhosts/blackbox_exporter/defaults/main.yml` | 探测目标按环境派生 |
| `roles/vhosts/vector-agent/templates/vector.toml.j2` | company/project/env 标签参数化 |
| `roles/vhosts/agent-svc-plus/defaults/main.yml` | **未改**，但默认值同样指向生产，见 B 节 |
