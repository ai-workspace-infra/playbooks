# playbooks Agent 约束：主机与服务 Roles

五仓职责完整判定见
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

## 声明驱动的 bootstrap 与验收

- 只消费本次已解析并记录 commit 的 GitOps 期望配置和 IaC CMDB 事实。清晰定义支持字段、类型与映射；不能仅转发旧前缀而静默丢弃身份等新增配置，也不能维护本地环境 YAML 副本。
- 禁止在 bootstrap、role、默认变量或脚本中写死环境批次、节点、云账号、state 路径、生产域名、Flux 根/namespace/TLS/ESO 对象清单。不得按名称/目录后缀推断行为；明确参数化能力和失败条件。
- 声明中的根名称、节点 hostname、批次目录和 source path 分开消费。修改主机名前核验现存 K3s/PV 身份；已有状态不一致时停止，不重绑持久卷或强行重命名。
- common 分别验证 hostname、OS 三账户/组、SSH 允许/拒绝和声明要求的安全基线；账号存在和旧节点回执均不能替代新节点完整验收。主机内防火墙归本仓，云防火墙归 IaC。
- K3s 初始化后读取真实 cluster UID，master 生成独立密钥、证书和私有 kubeconfig，校验信任链并以受控 Vault 身份写入声明路径；CA 私钥留在 master，不从 CMDB/预置配置交付。RBAC 的应用顺序须消除与身份验证的循环依赖。
- OS、Kubernetes 和数据库凭据各自授权。真实主体的认证及允许/拒绝验收与 Flux 接管、服务/数据验收分别记录；缺少必填参数或权限时明确失败，不回退到管理员。
- 本仓拥有主机、服务、数据库、备份恢复、迁移及受控 Vault 管理配置执行；不在此解析环境选择规则、生成云事实或操作云资源/DNS/State。简单任务优先 Ansible + YAML，复杂脚本须属于具体 role 并有明确输入输出。
- 通用入口、调用方和回执消费者验证完成后，删除旧实现、旧常量、旧测试期待和过期文档；不得增加版本专用兼容层、别名包装或静默 fallback。历史账号和持久资源的退役独立处理。

详细交接与硬编码判定见：[declaration-and-handoff](https://github.com/ai-workspace-lab/xworkspace-core-skills/blob/main/skills/engineering-standards/execution-ownership-migration/references/declaration-and-handoff.md)。
