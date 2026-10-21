# 对接 PROD V4 的固定版本节点部署入口

本入口对应 Knowledge《PROD V4 运行基线与快照清单》的控制面合同。该文档是有采样时间边界的证据；当前期望状态必须从本次固定 GitOps commit 读取，不能把历史 r8 当作永久最新版本。

入口状态：实现了固定源码、清单校验及精确节点选择；尚未形成已验收的 V4 节点发布清单。禁止将本入口标记为七节点新机部署通过。本入口不发布镜像或 tag，不切换控制面 Doco-CD，不修改 DNS。

## 使用方式

在维护主机通过 SSH/Ansible 部署，或在已准备依赖的 VPS 上使用审核过的 local inventory。需要 Git、Python 3.9+、Ansible、现有 Vault 登录及 SSH 信任。缺依赖即停止，不自动运行浮动安装脚本。

```bash
export PLAYBOOKS_COMMIT='<包含本入口并经过审查的40位commit>'
export GITOPS_COMMIT='<包含节点发布合同的40位commit>'
export NODE_RELEASE_MANIFEST='<GitOps内的节点发布合同.json>'
bash scripts/bootstrap-pinned-xconnect.sh --mode plan
bash scripts/bootstrap-pinned-xconnect.sh --mode check
bash scripts/bootstrap-pinned-xconnect.sh --mode apply
```

上述路径从固定 Playbooks checkout 执行；发布前不提供指向 main/latest 的 curl 安装命令。发布后可从对应 tag/SHA 下载同一入口。默认 plan 不连接节点；check 对所有步骤执行 Ansible check mode；apply 先对全部步骤完成语法和精确目标检查，再依次执行。宿主配置、凭据和服务操作仍由引用的 Playbook/Role 负责。

## GitOps 合同

JSON schema_version=1，必需字段：playbooks_commit、node、profile、inventory、artifacts、steps。inventory 为 GitOps 内已有的审核入口文件，不由此脚本合成主机身份；IaC 清单需要走既有 CMDB 交接。每个 step 含 phase（prepare/deploy/validate）、playbook（Playbooks 相对路径）、vars（非敏感参数）。最后一步必须 validate。每个 artifact 含不可变 tag 和 SHA-256 checksum/digest；Owner Role 必须在使用构件前比对 checksum，入口只验证合同完整性。

managed profile 为 shared/plus/team，必须给出 accounts_url、billing_url；team 必须带 team_id，其他 profile 不得带。standalone 不依赖控制面。这里的 profile 是清单校验，不能替代 Owner Role 的身份、授权及模板校验。pro 是权益范围，不把它自动变成独立物理资源池。

生产清单还必须逐项证明：

- Accounts/Billing/Console 的实际公共端点和版本 SNI；Accounts/Billing 按 API 验证；数据库使用既有隧道，不把 DB 别名当 HTTPS。
- 共享节点凭据仅由 Owner 从 Vault `kv/prod/accounts/runtime` 的 `INTERNAL_SERVICE_TOKEN` 解析。团队专属凭据及 scope 必须有实际受审合同，不能复用共享令牌来冒充团队专属授权。
- Exporter/Vector 配置 0600，空值 fail-closed；同输入不重启。向 Vector 统一上报，禁用 Agent 绕过 Vector 的账务采集。
- 流量链路验证 Exporter → Vector → Billing → PostgreSQL → Accounts；新鲜快照、HTTP 成功、分钟桶/账本非零增量和当前用户归属分别留证。无真实流量时不能伪造非零账本。
- Caddy 正式入口允许受鉴权的状态及采集 POST，避免 probe-only 403；证书来源、只读挂载、公共/版本 SNI 均检查。
- Doco-CD 负责 V4 控制面固定声明收敛；节点现有 systemd 服务继续由 Owner Role 管理。节点安装成功不代表控制面已更新。

## 当前需补齐的发布门禁

旧 setup-proxy.sh 仍会下载 main 模板及浮动 Xray 安装脚本；setup-observability.sh 有旧 Playbooks pin，Billing 快照默认关闭。现有 deploy_xray_proxy_server.yml 的随机 UUID/Vault 写回和默认账务直报也不能直接作为本入口的默认 prepare 步骤。

必须先为每种 profile 收敛上述 Owner 行为、固定构件并提供对应 GitOps 合同，再验证全新部署与同版本重跑；团队专属凭据、standalone 以及真实账务验证尚未执行。本次仅交付入口及合同检查，不将历史七节点 Token 修复的成功扩展为完整新机验收。

回退使用保留的上一版合同及构件，经 Owner 恢复配置；不删除快照缓存、不重置 Xray 计数、不修改历史账本。
