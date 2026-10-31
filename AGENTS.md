# playbooks Agent 约束：主机与服务 Roles

四边界完整判定见
[`execution-ownership-migration`](https://github.com/ai-workspace-lab/xworkspace-core-skills/blob/main/skills/engineering-standards/execution-ownership-migration/SKILL.md)。

## 允许内容

Ansible Roles/Playbooks 及其 reusable workflows：主机初始化、服务部署、Caddy/证书恢复、Xray/代理、Observability 服务、
数据库迁移、备份/隔离恢复、服务诊断和健康检查。主机目标可来自 IaC 生成的 CMDB；显式运维执行也可使用云平台控制面实时核实后生成的单次临时 inventory。秘密只经 OIDC→Vault 运行时读取。

## 硬禁令

- 不创建/销毁/修改云资源、DNS、Registry、OS Login、临时防火墙或 Terraform state；
- 不生成或篡改 CMDB 权威事实；不以 Ansible 代替 IaC；
- 不把 GitOps 声明复制成第二套持久人工 inventory/拓扑；单次临时 inventory 必须来自云平台控制面已验证的精确资源事实，并限定目标及用途；
- 临时 inventory 应记录云项目、区域/可用区、实例 ID、目标地址、采集时间及 SSH 主机密钥身份；仅用于本次操作，限制文件权限并在执行后删除，不得冒充 CMDB 或复用为后续权威清单；
- 云资源动作、DNS 变更和 state 操作必须回到 IaC Modules；控制入口、审批、证据放行必须回到 Toolkit。

数据库和 Observability 的执行逻辑归 Playbooks，但 workflow 入口可以由 Toolkit 通过固定 SHA reusable workflow 调用。
新增 Role 后必须先完成 owner → caller → 验证 → 删除旧副本，不能保留双重执行路径。

## 跨仓职责与 bootstrap 约束

Platform-Orchestrator 负责输入、授权、DAG、派发、等待和回执汇总；IaC 负责云资源、DNS、State 和 CMDB；GitOps 负责环境、资源和 Flux 声明；Artifacts 负责构建、镜像、Chart 和制品发布；Playbooks 负责主机、服务、数据库、备份恢复和迁移执行。

禁止在 Playbooks bootstrap 脚本或 role 中硬编码环境批次、节点、云账号、state 路径及其对应配置，也禁止复制 GitOps 声明形成第二套环境配置。目标与资源事实从 IaC CMDB 获取；期望配置通过本次解析并记录 commit 的 GitOps 声明传入；凭据从受控 Vault 身份获取。先补齐可复用 owner 入口与调用链并验证，再移除旧实现，不保留新的版本专用兼容路径。
