# Case Runbooks

真实发生过的故障复盘。每篇一个 case，包含：现象 → 证据链 → 根因 → 修复 →
验证清单 → 回滚 → 预防。

和 `docs/tasks/`（做什么）、`roles/*/README.md`（怎么用）的区别是：
这里记录的是**当时是怎么被骗的**——排查过程中走过的错路、以及下次快速判别的抓手。
写给下一个撞上同类现象的人（含 AI agent），所以命令要能直接复制执行。

命名：`YYYY-MM-DD-<短横线小写摘要>.md`

| 日期 | Case | 根因归属 |
|---|---|---|
| 2026-07-31 | [console 域 `/api/auth/*` 被劫持到 accounts，MFA 绑定面板密钥/二维码空白](2026-07-31-console-api-auth-bff-hijack.md) | `roles/vhosts/web_saas_host_config` Caddyfile.j2 |
| 2026-08-16 | [UAT agent-proxy Xray 因共享 access.log 权限错误未启动](2026-08-16-uat-xray-access-log-permission-denied.md) | `roles/vhosts/xray-exporter` tasks/main.yml |
