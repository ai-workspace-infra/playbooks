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
- `apt-listchanges.service`

这只停止本次已运行的后台任务，不永久修改系统 timer 策略。常驻的
`unattended-upgrades.service` 不纳入停止列表，因为 Debian 上它通常是
`unattended-upgrade-shutdown --wait-for-signal`，不是包管理锁持有者，停止它反而
会让 Bootstrap 等待服务退出。Debian/Ubuntu 的 Docker apt 操作统一设置
`lock_timeout`（默认 900 秒），并使用非交互的 `APT_LISTCHANGES_FRONTEND=none`，
为短暂锁竞争提供可控等待。

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

## 第二次 UAT 失败与微调

在使用 `playbooks_ref=main` 的 UAT run `30785772759` 中，apt 锁问题已不再
是失败原因，但新建 PostgreSQL 容器存在启动竞态：容器内 Unix socket 已经能
查询数据库，TCP `127.0.0.1:5432` 尚未稳定。`Verify dedicated role TCP
authentication` 因没有显式 TCP readiness gate 而反复等待，最终失败并额外耗时
约 4 分 39 秒。

因此在同一修复 PR 中将数据库存在性检查改为 TCP，并在角色校验前增加
`pg_isready -h 127.0.0.1 -p 5432` 的幂等等待，同时为所有 TCP 查询设置
`PGCONNECT_TIMEOUT=5`。这样启动未就绪时会等待明确的 readiness 条件，不再把
连接竞态误报为密码错误或拖延到默认连接超时。
