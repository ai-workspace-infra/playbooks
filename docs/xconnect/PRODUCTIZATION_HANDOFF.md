# XConnect-One Overlay：Playbooks 实施计划

状态：Batch 00 / planning
长期特性分支：`codex/xconnect-overlay-productization`

产品系列和对外品牌统一为 **XConnect-One**；CLI、服务名、仓库名、分支名和稳定技术标识继续使用小写 `xconnect`。

## 仓库职责

本仓负责 XConnect-One Gateway Agent/Relay 的安装、升级、Secret 引用、系统服务、网络规则、监控和端到端部署验证。终端 Join、设备身份、地址租约和 ACL 事实状态不由 Ansible 维护。

现有可复用基线：

- `vpn-wireguard-over-vless.yml`
- `roles/vhosts/xworkmate_bridge_distributed_vpn`
- `scripts/verify-wireguard-over-vless-closure.sh`
- `scripts/check-wireguard-over-vless-closure-evidence.sh`
- `roles/vhosts/vpn-overlay/{wireguard,xray,vxlan,setup-dnat}`

## 目标边界

```text
Ansible
  ├─ 安装 WireGuard、Xray、Gateway Agent
  ├─ 配置节点身份、证书、Vault 引用和 systemd
  ├─ 配置内核、日志、监控和 bootstrap
  └─ 验证第一份控制面 snapshot 已成功应用

accounts.svc.plus
  └─ 动态设备、Peer、地址、路由、ACL 和 generation

xconnect-gateway-agent
  └─ 拉取签名 snapshot，执行 wg syncconf/nftables，ACK 或回滚
```

Ansible 不应因用户执行 `xconnect join` 而重跑，也不应继续把客户端 Peer 列表作为长期事实来源。

## 分批实施

### Batch 01：Gateway 契约与 Golden

- 固化当前 WireGuard/Xray 渲染结果作为 golden fixtures。
- 定义 GatewaySnapshot JSON Schema。
- 为节点、客户端、单接入转发路由建立语义 diff。
- 给现有 role 增加 `ansible-lint`、syntax-check 和 Molecule 基线。

退出条件：控制面候选 snapshot 与当前 `group_vars` 渲染的 Peer、AllowedIPs、路由集合一致。

### Batch 02：Gateway Agent Shadow Mode

- 新增 `roles/vhosts/xconnect-gateway`。
- 安装 `xconnect-gateway-agent` 和独立 systemd unit。
- 配置节点短期凭据、控制面地址、签名公钥和 last-known-good 目录。
- Agent 只拉取、验证和生成候选配置，不修改运行时。

退出条件：两台 Gateway 均持续产出 `static == projected` 的机器可读证据。

### Batch 03：动态 WireGuard Peers

- 使用完整期望状态和 `wg syncconf` 原子更新动态 Peer。
- Gateway 自身和节点间 bootstrap Peer 仍由 Ansible管理。
- 应用失败自动恢复 last-known-good。
- 上报 generation、hash 和 apply result。

退出条件：新设备 Join 和撤销不再需要运行 Ansible，现有数据面在控制面离线时继续工作。

### Batch 04：动态 ACL

- Linux Gateway 首期使用 nftables atomic ruleset/set swap。
- ACL apply 与 WireGuard snapshot generation 一致。
- 管理面连接使用保护规则，避免业务 ACL 切断控制面。
- 导出 rule ID 级 deny 指标和脱敏审计日志。

退出条件：禁用客户端本地策略仍不能越权；失败更新不会替换上一份有效规则。

### Batch 05：静态列表退役

- 删除 `xworkmate_bridge_distributed_vpn_clients` 的运行时依赖。
- 保留一次性 importer 和一个发布周期的兼容检查。
- 删除普通终端执行服务器 Playbook 的产品路径。
- 更新部署、升级、故障恢复和 closure runbook。

退出条件：inventory 只保存 Gateway 静态身份和 bootstrap 数据；所有客户端状态来自控制面。

## 测试门禁

每个 Batch 至少执行：

```bash
ansible-lint
ansible-playbook -i inventory.ini xconnect-gateway.yml --syntax-check
molecule test
scripts/verify-xconnect-closure.sh
scripts/check-xconnect-closure-evidence.sh <evidence-dir>
```

关键场景：

- Agent 第一次启动和重复应用幂等。
- 错误签名、低 generation、过期 snapshot 被拒绝。
- 空 Peer snapshot 具有显式保护，不能意外清空运行网络。
- 控制面不可达时保持 last-known-good。
- 新设备加入、密钥轮换和撤销达到定义 SLA。
- 双 Gateway 单接入客户端返回路由正确。
- Xray、WireGuard、Agent 任意顺序重启后自动恢复。
- nftables 更新失败可回滚。

## PR 策略

- 所有编码 PR 的 base 为 `codex/xconnect-overlay-productization`。
- 每个 Batch 使用独立 `codex/xconnect-batch-NN-*` 分支。
- 未经维护者明确要求，不将编码 PR 指向 `main`。
- 每个 PR 必须列出测试 Case、evidence、feature flag 和 rollback。
