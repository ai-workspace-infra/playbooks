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

## Role 单一职责与依赖编排

- 一个 role 聚焦一项核心能力，例如 OS 基线、K3s 控制面、Flux 控制器、GitOps 交接或 Kubernetes 身份生命周期。关联任务可以分为 install/configure/validate；不得把多个独立组件的安装、配置和运维堆进同一 role。
- 入口 playbook 负责目标选择、输入绑定和角色调用顺序。确需保留 bootstrap 聚合 role 时，它只声明子 role 和依赖顺序，不直接执行 package、shell、文件写入、集群资源变更或凭据签发。
- 每个执行 role 的 README 必须说明唯一职责、目标组、输入、前置条件、输出/验收证据、幂等重试及回退边界。缺少依赖时明确失败，不隐式安装其他组件或回退成管理身份。
- 使用显式 `import_role`/`include_role` 表达有顺序的依赖。`meta/main.yml` 仅用于必须先执行且可安全复用的依赖；不要用自动依赖隐藏初始化次序、重复初始化或形成循环。K3s API 就绪后才能安装 Flux，声明交接后才能验证由 GitOps 授权的真实身份。
- OS hostname、用户与基线归 common；K3s 控制面 role 消费并校验这些前置事实。Flux 控制器安装与 GitOps source/root 交接分开；ESO、Ingress 和业务 HelmRelease 的调谐依赖由 GitOps 声明，不塞回 bootstrap。
- 默认变量、模板、执行脚本和 handler 随所属能力放在对应 role 内；共享输入合同通过显式变量传递，不跨 role 读取内部 tasks/files，也不复制实现。重构必须同步入口、脚本包装器、测试、CI 路径过滤和文档引用。
- 拆分前盘点调用方与输出合同；拆分后验证依赖顺序、独立入口、缺参失败、凭据保护及已有验收。删除旧副本不能先于调用链验证，不创建版本专用别名或兼容实现。静态/本地测试通过不代表生产验收通过。

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
