# UAT Bootstrap apt 锁与执行时间记录

## 现象

UAT `console-uat.onwalk.net` 的 Bootstrap 在安装 Docker 依赖期间耗时约 14 分钟。主机只读检查发现：

- `apt-listchanges.service` 长时间处于 `activating`；
- `apt-listchanges` 由系统 timer 触发，和主机初始化阶段的 Docker/基础包安装争用 APT/DPKG 状态；
- PostgreSQL、Docker 本身可用，Accounts 的重启是此前 `users` 基线表尚未初始化的后果，不是本次 apt 异常的根因。

## 修复

`roles/vhosts/docker` 在首次 Docker 包操作前幂等停止当前运行的后台 APT 服务：

- `apt-daily.service`
- `apt-daily-upgrade.service`
- `unattended-upgrades.service`
- `apt-listchanges.service`

这只停止本次已运行的后台任务，不永久修改系统 timer 策略。Debian/Ubuntu 的 Docker apt 操作统一设置 `lock_timeout`（默认 900 秒），并使用非交互的 `APT_LISTCHANGES_FRONTEND=none`，为短暂锁竞争提供可控等待。

## 验证

- `ansible-playbook --syntax-check setup-web-saas-domain.yml`：通过。
- `git diff --check`：通过。
- 未直接停止 UAT 主机上的进程，未修改数据库，未触碰生产节点。

## 后续验收

使用该 playbooks PR 重跑 UAT 时，重点记录：

1. Console Bootstrap 是否从约 14 分钟降至正常的包安装耗时；
2. DB Init 是否能继续执行 `Create Web SaaS databases and roles` 与 `Initialize Web SaaS baseline schemas`；
3. Vault token 读取是否保持成功，避免回退到随机密码；
4. Accounts 是否停止因 `users` 表不存在而重启。
