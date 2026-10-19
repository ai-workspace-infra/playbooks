# V3 VLESS 传输路径 P0 修复发布验收

日期：2026-10-19（Asia/Shanghai）。V3 Accounts 新容器启动于 23:02:02。
执行路径：SSH → Playbooks → GitOps → Doco-CD。未 dispatch CI 部署。
使用已经发布的 main 源码镜像，不在目标主机编译。

## 问题与修复

Accounts 曾将 `resourceClass` 拼入 XHTTP 路径，产生 `/pool/plus/split`，
但节点实际传输路径为 `/split`。Accounts
[PR #250](https://github.com/ai-workspace-services/accounts/pull/250) 已合并为
`fe7a146c4c2bdc94e9d4f80cb3800e245eee54a5`：资源类别继续用于授权、筛选和分类，
路径仅由传输配置决定，默认 `/split`。修复覆盖共享节点和专属配置使用的共同路径函数。

本次没有更改 Agent、Xray、DNS、用户属性、配额或账务记录。
没有通过真实用户/测试用户写入来生成验收链接。

## 不可变构件与回退

- GitOps [PR #474](https://github.com/ai-workspace-infra/gitops/pull/474)：已合并。
- GitOps SHA：`28044ec777e1ed012ad58aeb4577db0403719d4f`。
- Release：`prod-v3-vless-path-2026.10.19-r1`。
- Doco-CD target：`prod-v3-stripe-sandbox`。
- Accounts source SHA：`fe7a146c4c2bdc94e9d4f80cb3800e245eee54a5`。
- `service_image_ref`：`ghcr.io/ai-workspace-services/accounts:sha-fe7a146c4c2bdc94e9d4f80cb3800e245eee54a5@sha256:a8e1b425509e036bef38a3e80bbabb0219ddb0870ca39fb8a02bfaf067fdf2e8`。
- 前一发布：`prod-v3-service-token-2026.10.19-r1`；Accounts 回退 digest：
  `sha256:7c1ec4a1bd246b4d18046e20a8792c413bf72f824b635766087bf8d6339c6ffc`。
- 数据库版本沿用 `2026101902`；新旧源码之间没有 SQL 文件差异。
- SQL checksum、备份/恢复结果、业务数据指纹：本次无数据库变更，不适用；
  不把健康检查当作全量数据一致性证明。

本轮是当前 V3 原位应用修复，未进行 V2→V3 数据追齐或写入权移交。
回退仅针对应用发布；旧镜像会恢复错误的路径生成行为，不执行数据库 down migration。

## 执行与验证

1. Accounts 干净独立 checkout 固定合并提交；`go test ./api ./internal/store` 和
   `git diff --check` 通过。代码 PR 及 GitOps PR 的安全/发布检查通过。
2. GHCR manifest 拉取核验得到上述 digest。未将 image config digest 当作 manifest digest。
3. 使用 IaC owner 的 inventory 生成器，从已保留 Terraform state 的 runtime 输出与
   已审核声明重建 CMDB，目标为 V3；通过已验证主机密钥的 SSH 执行
   `select-doco-cd-release.yml`，固定 tag、repository、target。
4. Doco-CD 日志已观察到 release tag 和 GitOps SHA，无 pull error；运行 Accounts
   的固定引用与上述 `service_image_ref` 一致。
5. 公共 `accounts.svc.plus` 与 V3 别名的 `/api/ping` 均通过仓库
   `validate-deploy.sh` 的完整 image/tag/commit/version 匹配检查。
6. V3 Accounts `/readyz` 返回 `database=ok, status=ready`；公共 Console `/panel`
   为 307，Billing `/readyz` 为 200，TLS 校验通过。
7. Accounts 重启计数为 0；Billing、Console、PostgreSQL 的镜像及启动时间未变。

## 用户侧刷新边界

代码测试和运行版本验收已完成。未自动验证登录后的真实二维码和终端连接。
刷新 Portal 重新获取节点、重新生成二维码/链接后，应得到 `path=%2Fsplit`；
已经复制的链接、下载文件或客户端缓存不会自行替换，需要重新获取受管配置。
Agent 注册/实时报告及区域入口完整性是独立验收项，本次镜像更新不能替代它们。
