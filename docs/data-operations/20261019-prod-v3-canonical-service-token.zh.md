# V3 共享服务令牌来源修正与部署验收

验收时间：2026-10-19 20:54（Asia/Shanghai）。执行路径：SSH → Playbooks → GitOps → Doco-CD。
仅在 V3 发布；没有 dispatch GitHub Actions 部署。PR 检查为代码门禁。

## 权威来源与修复

统一来源为 `kv/prod/accounts/runtime` 的 `INTERNAL_SERVICE_TOKEN`。
只读比对确认该 Key 已与 V2 原值相同，本次未写 Vault 或轮换凭据。

V3 原先使用恢复 Role 首次生成的独立令牌，Console 未配置此变量。
Playbooks 现在从固定 GitOps 声明读取上述路径和 Key，写入权限 `0600` 的
`/etc/xcontrol/web-saas-recovery/internal-service.env`。Accounts、Billing、Console
均加载此文件；Accounts Sandbox 覆盖配置也将它放在最后。Console 不加载数据库
和 Stripe 环境文件。旧恢复凭据文件保留，服务令牌由专用文件覆盖。

恢复数据库密码及登录凭据仍独立管理；本次没有重新恢复数据库。
缺失/非法令牌或错误路径会停止执行；不生成替代共享令牌。

## 固定构件

- Playbooks [PR #695](https://github.com/ai-workspace-infra/playbooks/pull/695)：已合并。
- Playbooks SHA：`fb3089923c3be1e9a38174c804a503ee9f4cad74`。
- GitOps [PR #473](https://github.com/ai-workspace-infra/gitops/pull/473)：已合并。
- GitOps SHA：`5cf4910e818a6b061a5ff7edafbcc0543bc66fed`。
- Release tag：`prod-v3-service-token-2026.10.19-r1`。
- Doco-CD target：`prod-v3-stripe-sandbox`。

| 服务 | 本次保留的镜像 digest |
| --- | --- |
| Accounts | `sha256:7c1ec4a1bd246b4d18046e20a8792c413bf72f824b635766087bf8d6339c6ffc` |
| Billing | `sha256:462a7835f649ffad3bd88cb7e51e8e6ea4deb1f8fa6509f6a5ed5d9259058db4` |
| Console | `sha256:9fc0fb54fb680d553fbce23bff1ca1bebba60b070d039ff76ce43d3a035f15b4` |
| PostgreSQL | `sha256:f97857bd59c1fc55e3be3942b2c8f5554a8e9ee1d08bf3e834940c33189a141c` |

应用 source SHA 沿用前次[恢复发布记录](v2-to-v3-snapshot-refresh-20261019.zh.md)，未重建镜像。
SQL checksum、迁移前后版本、DB 备份/恢复结果、数据指纹：本次不适用，未执行数据库操作。

## 已执行验收

1. 新增/使用 `web-saas-service-token.yml`，限定一个 IaC CMDB V3 主机，验证真实
   hostname、既有私有配置及精确干净 GitOps checkout，再从 Vault 读取令牌。
2. 准备仅新增专用凭据文件；随后由现有 release selection Role 选择不可变 tag。
3. Doco-CD 日志已观察到上述 tag 和 GitOps SHA；三个应用容器重建，镜像不变。
4. 运行 `verify`，仅从 Vault 和运行容器读数，比较三个应用的令牌，结果一致；
   `changed=0`。跨代际内存比对亦确认 V2/V3 使用同一值。未记录值或其散列。
5. 三个应用及 Doco-CD 重启计数为 0；PostgreSQL 容器身份、启动时间保持不变。
6. V3 Console `/panel` 返回 307；Accounts/Billing `/readyz` 各 200；TLS 校验通过。
7. 公共 Console CNAME 仍指向 V2；本次未修改 DNS、V2 服务或远程 Agent。

本地四项测试通过：合法私密读取、空值/多行拒绝、代际路径拒绝、恢复随机种子
不改变共享令牌。Ansible 语法、Compose 最终环境文件顺序及差异检查通过。

## 边界及后续验收

共享令牌一致不等于 Agent 已向 V3 报告。Agent 实际 endpoint、独立 scoped credentials、
新鲜区域报告和登录后页面须单独核实。使用共享 fallback 的 Agent 部署调用方应从
相同 Vault Key 注入，已有独立 scoped credentials 不因本次改动被替换。

上一 release 为 `prod-v3-public-sni-2026.10.19-r1`，但直接回退会重新引入旧令牌及
Console 缺少令牌的问题；回退应用配置时需保留本次 canonical env 注入，不能宣称
旧声明可无条件回滚。数据库及历史备份不参与本次应用配置回退。
