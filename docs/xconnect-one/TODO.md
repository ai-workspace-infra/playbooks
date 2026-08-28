# XConnect-One Playbooks/Gateway TODO

状态日期：2026-08-28

## P0：Accounts authorization producer 集成

- [x] Accounts Batch 09 已实现与 Gateway Batch 06 canonical fields 一致的签名 producer
  （远端 `98edbbe`）；Gateway verifier/readiness 已在本仓 Batch 06 (`98c45b2`) 完成。
- [ ] 在 staging 部署 Accounts endpoint，使用真实内部 service token、独立 signer 私钥和
  Gateway root-owned pinned public key；禁止测试 seed 进入环境。
- [ ] 联调 current/previous key rotation、篡改、过期、重放、错误 node/network/generation、
  错误 baseline/projection/policy、无 successful apply 和 pending reconcile。
- [ ] 保存 authorization 原文的安全摘要、verifier decision、snapshot/apply/readback 和
  连续健康 evidence；未形成证据链前 readiness 通过也不能作为生产许可。

## P0：real staging soak

- [ ] 对受控静态 inventory 运行 dry-run import，人工审查 canonical hash 后提交，保存
  import receipt 和静态/Accounts/snapshot 三方集合对照。
- [ ] 长时间运行 shadow，记录新增/删除/address/key/policy diff、heartbeat、reconcile 和
  控制面短暂不可用行为。
- [ ] 在 maintenance window 执行 apply/readback/LKG，演练 Xray、WireGuard、nft、端口
  takeover 三阶段失败以及 quarantine/manual recovery。
- [ ] 显式切换 accounts-only 后执行 soak，再演练回 shadow/LKG；不得自动恢复撤销设备、
  轮换前 key、旧 VLESS credential 或旧服务。

## P1：静态 `group_vars` 退役

- [ ] soak 通过后先冻结静态客户端清单写入，确认 Join/Leave 只改变 Accounts 投影且
  不触发 Ansible 重跑。
- [ ] 审计 role/task/template 无动态 peer 的静态变量引用，保持 CI guard。
- [ ] 完成备份、恢复、rollback、operator approval 和变更窗口后，再用独立变更删除
  静态客户端列表；当前清单必须保留。
- [ ] 保留静态 schema、import receipt、diff 和迁移审计，不能用删除历史掩盖漂移。

## P1：E2E 与运维

- [ ] 联合真实 Accounts、Linux Gateway 和五平台客户端覆盖 join、sync、ACL allow/deny、
  rotate、suspend、revoke、leave、token replay、证书轮换和 Gateway rollback。
- [ ] 输出安装/升级/降级、signer rotation、LKG/quarantine、监控指标、告警和事故恢复
  runbook。
- [ ] 验证发布制品只包含 Xray-core runtime 路径；XConnect-One 角色不调用 sing-box。

## PR

- [ ] 当前没有新 PR。先核对长期分支和已有 PR，再提交到
  `codex/xconnect-overlay-productization`；实际创建前需再次取得用户确认。

