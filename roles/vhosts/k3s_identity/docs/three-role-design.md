# OS 三账户与 K3s/K8s 身份：最小实施设计

状态：**通用三身份 role 与 GitOps RBAC 声明已实现；本次补齐 operator 默认 kubectl 身份入口；目标集群真实认证/允许拒绝验收仍待执行**。本文适用于不同环境和集群，由 K3s/K8s 执行 role 参数化复用；GitOps 是权限和资源范围的声明源，Playbooks role 负责认证交付、应用和验收。本文不授予部署、数据迁移、DNS 或额外权限变更授权。

## 1. 通用边界与参数

- Linux 用户和同名主组统一为 `root:root`、`operator:operator`、`auditor:auditor`，不随环境或部署批次衍生用户名。每个目标的 OS 登录和权限单独验收；暂用同一公钥时须记录持钥人可选择登录全部账户，不能认定人员身份相互隔离。
- `vhosts/common` 只管 OS 账户、SSH、受控 sudo 和 OS 验收；Kubernetes 认证、kubeconfig、RBAC 与相关验收由 K3s/K8s 对应 role 处理。
- 遗留审计身份及其调用方由目标声明显式列出；不能只因新身份存在就删除。历史验收和当前目标的实时证据分别记录。
- 历史验收不代表实时权限；执行前只读核对当前 K3s、Flux、ServiceAccount、RoleBinding、ClusterRoleBinding、客户端凭据和消费者，记录时间及目标身份。
- 身份初始化不隐式调整来源环境、canonical DNS、业务写入权或有状态组件版本；业务发布与身份变更分开验收。

OS 方案参考 [common three-role-design.md](../../common/three-role-design.md)。

三账户与 Kubernetes 身份是 K3s platform bootstrap 的初始化范围：先由 `vhosts/common` 准备并验收 OS 账户，K3s role 在集群和 Flux 就绪后协调凭据交付及身份验收，GitOps 声明和调谐 RBAC，Vault 保存按集群区分的独立凭据。各阶段使用同一个已验证目标和固定声明；不因 OS 同名账户已存在就认定 Kubernetes 认证和权限已生效。现阶段三类 Kubernetes 身份联动尚未实施。

| 声明参数 | 用途 | 约束 |
| --- | --- | --- |
| 环境与稳定集群标识 | 选择目标、信任范围和凭据引用 | 显式必填；不从部署批次推导环境 |
| 部署批次 | 追溯本次创建/发布 | 仅作为记录元数据，不进入通用角色或主体命名 |
| runtime 与版本 | 选择 K3s/K8s 的实际认证适配 | 来自固定声明，不由身份 role 重装集群 |
| 三类主体与凭据引用 | 绑定 root/operator/auditor | 私钥与 token 存受控 Vault KV，GitOps 仅引用路径；凭据不跨集群复用 |
| 操作 namespace、资源和动作 | 定义 operator 边界 | 显式白名单，缺失时拒绝创建变更权限 |
| 审计读取与脱敏范围 | 定义 auditor 的迁移核对和安全审计 | 区分可直接读取的对象与需脱敏的输出 |
| 旧身份与消费者 | 确定渐进替换顺序 | 显式列出，不用固定遗留用户名作为默认值 |

上表是待实现的通用接口契约，不代表解析器已支持这些字段。历史目标可能已含批次后缀；执行前按其真实 ID 校验，不因泛化文档自动重命名现有节点、集群、state、namespace 或资源。


## 2. 三类身份及责任

| Linux 用户 | Kubernetes 认证主体（规划） | 主要职责 | 权限边界 |
| --- | --- | --- | --- |
| `root` | `root` | 聚焦基础：OS/集群初始化、恢复、Flux CD、GEO、Ingress、身份和权限管理 | 管理配置由 GitOps 声明，使用独立管理主体及明确 ClusterRole；审计危险操作；管理凭据不注入应用。主机 root 保留恢复入口，其实际能力不能被 Kubernetes RBAC 对抗。 |
| `operator` | `operator` | 聚焦业务：被授权服务的状态、发布和日常维护 | 业务配置及操作范围由 GitOps 动态管理，按 namespace、资源、动作和必要时资源名授权；不授予集群管理员、RBAC 提权、节点代理或直接读取 Secret。 |
| `auditor` | `auditor` | 聚焦迁移核对、在线分析、审计和安全评审 | 只读范围由 GitOps 动态管理，独立身份可查看授权配置和必要数据；禁止集群/业务状态变更、Secret、exec/attach、proxy、token 签发、模拟及提权；源数据读取另由数据库 owner 授权。 |

主体是独立的 Kubernetes 身份，不以 Linux 用户名、context 名或 `sudo kubectl` 冒充身份隔离。主体采用三个通用名字，OIDC 或证书的实际映射由固定 GitOps 声明驱动；不同集群与环境使用不同凭据及信任范围。集群身份和凭据版本参与校验，部署批次不参与用户名或 RBAC 名称拼接。

**root 的例外说明**：K3s 默认管理 kubeconfig 是高权限恢复入口，OS root 可以访问集群 CA 和管理员文件，因此不能声称对 OS root 有强制集群权限隔离。目标是给日常管理提供独立且范围受限的身份，保留现有管理入口供受控恢复；在验证新身份与消费者前不删除或改写旧凭据。参见 [K3s cluster access](https://docs.k3s.io/cluster-access)。

这里的 `operator` 是日常业务运维身份，应用进程仍使用各服务独立的非 root 身份和凭据；不把共享运维账户变成全部业务的运行身份。`GEO` 暂沿用需求名称，具体组件及 API 资源清单待确认，不自动解释成 ESO。

## 3. GitOps 与 Playbooks 责任

| 层 | GitOps 声明 | Playbooks role 执行 |
| --- | --- | --- |
| 身份映射 | 目标集群、Linux 用户、独立 Kubernetes 主体、凭据提供方引用、namespace 和策略引用；不含 token/私钥 | 校验主机与集群 UID、读取固定声明、对接受控身份颁发和轮换 owner；确保实际主体与声明相同 |
| RBAC | 明确 Role/ClusterRole、RoleBinding/ClusterRoleBinding，限定 API group、resources、verbs、namespace、必要的 resourceNames | 验证固定版本与实际生效结果；若由 Flux 管理，等待对应 revision 收敛并取证，不与 Flux 争抢对象所有权 |
| 准入 | 声明允许的 workload 操作及准入约束，固定策略版本与例外 | 等待准入组件可用，运行允许/拒绝验收；约束不可用时禁止放开模板修改 |
| 客户端配置 | 只声明凭据引用和有效期/轮换规则 | 安全生成或交付专属 kubeconfig，按用户保护目录与文件；不复制管理员私钥和管理员 kubeconfig |
| 验收 | 定义允许/拒绝用例和目标范围 | 用真实拟用身份连接实际 API，输出脱敏结果，关联集群 UID、声明 SHA、执行版本和 run ID |

配置源由调用方提供，当前仓库为 `https://github.com/ai-workspace-infra/gitops.git`。GitOps 使用通用身份/RBAC 基础声明，环境与集群 overlay 仅选择真实目标、namespace、维护对象和凭据引用；不复制一套按部署批次命名的角色或策略。身份/权限单元独立固定版本和暂停状态，其管理 owner 必须明确；**目录和对象尚未创建，不能视为已有发布单元**。不要在暂停声明加载器、根调谐与 Playbooks 中重复管理同一 RBAC 对象。

### 3.1 基础、业务和审计的动态配置

动态配置是修改 GitOps 声明、审查并发布固定版本，再由受限执行身份调谐生效；不是把权限硬编码进 role，也不是授予使用者自行扩权。Git 仓库提交/合并权限与 Kubernetes RBAC 分别授权，SSH 用户名不直接决定仓库权限。

| 配置范围 | GitOps 可更新内容 | 审查与生效边界 |
| --- | --- | --- |
| 基础管理：root | Flux CD 的 source/root/调谐配置、GEO、Ingress、集群基础组件、身份映射、RBAC 和准入策略 | 管理员审查发布；执行控制器使用明确的管理权限。业务发布入口不能改写基础配置、权限或控制器执行身份 |
| 业务运行：operator | 授权业务的镜像、应用配置、发布版本及允许的维护操作；namespace、资源和动作白名单可按需调整 | operator 可通过授权业务入口提交变更；权限白名单及新增授权由管理员审查发布。业务调谐使用受限执行身份，不能通过业务声明创建 RBAC 或部署高权限工作负载 |
| 迁移/审计/安全评审：auditor | 可见 namespace、资源种类、配置引用、脱敏报告/日志/指标范围及业务系统迁移所需的源端读取、授权导出和目标只读核验项 | auditor 可提出读取需求、评审或报告定义变更；管理员与对应数据 owner 审查发布。可用于业务系统迁移的只读阶段；auditor 本身始终无系统/业务变更权限，也不能修改自己的授权、凭据或执行身份 |

- 基础、业务和审计授权分别明确声明 owner 和调谐范围；权限配置仅由管理员管理。可以继续使用同一仓库，但不能让业务输入修改 source 引用、根对象、基础单元、RBAC 或准入策略；仓库审查规则与受限调谐权限共同约束边界。
- 纯 RBAC 范围变更由 Flux 调谐，不要求重新执行 OS 初始化或重新签发身份；认证映射、信任或凭据变化才由 K3s/K8s role 对接对应交付与轮换流程。Playbooks 读取固定声明并验证收敛，不另存可漂移的权限清单。
- 每次扩大或收缩权限都记录声明 revision，使用真实身份验证新增允许及越权拒绝；撤销范围需验证已不可访问。配置未收敛、证据失败或仍有额外旧绑定时，不认定变更完成。
- 审计查询/报告定义若由自动化执行，执行身份同样仅有经审查的源读取权限；报告输出写入指定位置不能成为修改系统或业务数据的通道。数据库读取与迁移目标写入仍由对应 role 分别授权。

## 4. 认证、kubeconfig 和生命周期

1. 先核实实际 K3s 支持的认证机制和受控颁发 owner，再选定独立、可过期、可撤销的人员身份；候选为现有受控 OIDC 或由 owner 签发的短期客户端证书。OIDC issuer/audience、CA 信任、签名和有效期必须验证，不能仅声明名称。
2. 若选客户端证书，分别绑定独立 Kubernetes 用户或组，**不得把 operator/auditor 加入 `system:masters`**。私钥在受控环境生成，只有对应用户可读；CA 私钥不交给 operator 或 auditor。证书撤销能力及轮换方式要按实际 K3s 验证；短有效期和移除绑定用于收敛风险，不假定 CRL 已启用。
3. 三个 kubeconfig 分别指向各自真实凭据和已验证的集群 CA/API 地址；目录 `0700`、文件 `0600`，并检查父目录、ACL、实际读取与 Unix 用户所有权。不能把管理员 `k3s.yaml` 拷贝给其他用户。OS `operator` 的 `~/.kube/config` 只可由 K3s identity role 在确认目标不存在或已是受管同目标软链接后指向 `identity-config`；存在不受管文件时停止，不覆盖。这样 `kubectl` 默认使用受限 operator 身份，不通过 OS sudo、K3s 管理 kubeconfig 或 containerd socket 提权。
4. 不使用长期静态 ServiceAccount token 作为人员登录凭据；也不把 Vault token 或集群私钥提交 Git、打印日志或放入不受保护的 artifact。
5. 建立签发、有效期、轮换、撤销及凭据遗失处理；身份到期、目标变化、RBAC 或准入变化后，对应验收证据失效并需重验。

### 4.1 Vault KV 凭据路径

按本次约定，以环境和集群分别作为路径层级，每类身份单独一条记录：`kv/<environment>/<cluster>/<role>`。这是 K3s/K8s 客户端凭据的规划，不改动 OS SSH 公钥现有的 `kv/<environment>/security/{root,operator,auditor}`，也不收集 OS SSH 私钥。

| 身份 | Vault KV 逻辑路径 | 存储用途 |
| --- | --- | --- |
| root | `kv/<environment>/<cluster>/root` | 独立、限定管理范围的 Kubernetes 客户端凭据；不存默认全权管理 kubeconfig |
| operator | `kv/<environment>/<cluster>/operator` | 业务运维身份的独立 Kubernetes 客户端凭据 |
| auditor | `kv/<environment>/<cluster>/auditor` | 迁移核对、审计和安全评审所用只读身份的独立 Kubernetes 客户端凭据 |

例如 `environment=prod`、`cluster=core-a` 时，路径分别为 `kv/prod/core-a/root`、`kv/prod/core-a/operator`、`kv/prod/core-a/auditor`。环境与集群来自同一份已审查声明，校验各层允许值及环境与集群组合的唯一性，不从当前主机名或部署批次自动拼接。不同独立集群必须有不同的集群标识及路径；记录同时绑定真实集群 UID，UID 不符时拒绝消费，不沿用旧集群凭据。此示例不重命名已有目标。

以下字段是待实现的存储契约，按实际认证方式选择，不要求每条记录同时含私钥和 token：

| 字段 | 内容与约束 |
| --- | --- |
| `environment`、`cluster`、`cluster_uid`、`subject` | 凭据目标与真实认证主体；必须与调用声明及实际集群一致 |
| `auth_type` | 受控认证方式；与颁发流程一致，不允许凭据缺失时改用管理员认证 |
| `client_private_key`、`client_certificate` | 客户端证书方式所需的独立私钥及证书链；不存集群 CA 私钥 |
| `token`、`expires_at` | 仅在所选认证方式确需交付短期 Kubernetes 客户端 token 时使用；记录有效期，过期即拒绝。优先运行时获取，不将长期 ServiceAccount token 或 Vault 登录 token 存入此处 |
| `issued_at`、`credential_id`、`owner`、`rotation_due_at` | 签发、消费与轮换追踪；证书有效期同样必须检查，不只依赖 KV 元数据 |

KV v2 中 `kv` 为 mount：逻辑路径 `kv/prod/core-a/auditor` 对应读取/策略路径 `kv/data/prod/core-a/auditor`；元数据路径为 `kv/metadata/prod/core-a/auditor`，不能混用逻辑路径与 API 路径。GitOps 只保存逻辑路径、认证方式及必要的非敏感引用，不保存字段值。

初始化脚本 `scripts/bootstrap_k3s_identity_keys.sh` 调用 role 中唯一的节点侧生成器，输入环境、集群、真实集群标记、API 地址、master 的客户端 CA/密钥与服务端 CA 路径，以及受保护状态目录。脚本在节点签发三套独立短期凭据、验证链与密钥匹配、生成私有 kubeconfig，再以 CAS=0 写入和回读 Vault。无需预置 JSON。相同目标的有效本地状态与 Vault 记录可复用；部分签发、不同记录或过期凭据拒绝自动覆盖，需管理者检查。多条 Vault 写入非原子，失败时保留节点受保护状态以供安全重试。CA 私钥不进入 Vault 角色记录。上述代码已实现并通过本地测试，尚未完成实际 Vault 和集群身份验收。

### 4.2 与 IaC、K3s bootstrap 的交接

| 步骤 | owner | 输入与输出 | 当前状态 |
| --- | --- | --- | --- |
| 1. 声明目标 | GitOps | 声明环境、集群标识、节点及身份策略引用；固定 Git revision | 身份契约尚未接入 |
| 2. 创建主机与独立存储 | IaC | 消费目标声明；输出实例 ID、地址、磁盘等 CMDB 事实，供 Ansible 选择目标 | 现有 IaC/CMDB 交接；IaC 不负责签发客户端凭据 |
| 3. OS 与平台初始化 | `vhosts/common`、bootstrap 编排器及独立执行 role、Flux | common 初始化 OS；`k3s-cluster-server` 初始化控制面，`flux_controllers` 安装控制器，`flux_gitops` 交接声明并记录实际 revision；ESO、Ingress 由 GitOps 调谐 | 执行职责已拆分；拆分后的生产运行证据待取得 |
| 4. 节点生成与归档凭据 | K3s role、节点侧初始化脚本 | master 生成三份独立密钥及客户端证书，验证信任链并生成私有 kubeconfig，写入各角色 Vault KV；CA 私钥留在 master | 节点侧生成、签发、归档及 role 调用已实现；真实 Vault/集群验收待执行 |
| 5. 权限与凭据交付 | Flux、`vhosts/k3s_identity` | Flux 收敛审查后的 RBAC；role 将 master 生成、已归档且读回确认的 kubeconfig 交付对应 OS 用户 | 交付代码已实现；GitOps RBAC 就绪、Vault writer 授权及真实交付仍需运行证据 |
| 6. 验收与轮换 | `vhosts/k3s_identity`、身份 owner | 使用三套真实身份验证允许/拒绝；后续按凭据 owner 流程轮换 | whoami、声明允许/拒绝和强制敏感操作拒绝探针已实现；真实验收待执行，自动轮换未实现 |

交接 verifier 将 `kube-system` namespace 的 `metadata.uid` 作为 receipt 中的 `cluster_uid` 标记；它是本项目选择的运行时标识，并非 Kubernetes 原生的集群 UID，也不是 IaC 实例 ID。后续交接必须先验证该 receipt 与目标实例和声明匹配，并明确此标记的生命周期：namespace 或集群重建会改变它，需要重新绑定和验证，不能自动复用旧凭据。初始化脚本会连接实际集群核对该 UID；调用者仍需核验 receipt、CMDB 与固定声明的绑定。

K3s 首次启动和客户端签发之间不能形成循环依赖：先使用受控 bootstrap 管理入口建立并核验集群，再由 K3s role 在 master 节点生成各角色独立私钥，经受控管理员签发路径签发短期客户端证书，验证客户端信任链和密钥匹配，生成各用户私有 kubeconfig，并将角色凭据写入 Vault，最后验收新身份。凭据在节点初始化阶段生成，不要求预置三份 JSON 或提前写入 Vault。GitOps 中只放非敏感路径与 RBAC 声明，IaC state、CMDB、Git 仓库和普通日志都不承载私钥。不得将 CA 私钥交给日常 operator 或 auditor，也不得以已有管理员 kubeconfig 冒充三套独立身份。

ESO 与 Ingress 均属于平台初始化范围，但由各自的 GitOps 发布单元声明和调谐，避免 Playbooks 和 Flux 双重管理。集群代际替换时先初始化 ESO 并完成 Vault TLS 前置，再依次部署 Vault、PostgreSQL、Observability 和业务，最后初始化并验证 Ingress；不自动启用或修改 DNS。

- 凭据颁发/轮换 owner 负责写入；消费身份仅获精确记录的 `read`，不授予通配读取、目录 list、写入、删除或读取其他角色/环境/集群的权限。人员与交付任务分别授权，不给整个业务调谐控制器默认发放这些凭据。
- auditor 读取自身认证凭据是建立独立只读连接的必要入口，不等于可读取 Kubernetes Secret、其他角色凭据或业务秘密。其自身记录的更新由受控 owner 完成，不能自行修改主体、权限或有效期。
- Playbooks 通过受控 Vault 身份运行时读取指定版本，验证目标及有效期，再交付对应用户的受保护 kubeconfig；秘密使用 `no_log`，不进入 Git、日志、普通缓存或 artifact。人用动态认证仍优先在客户端运行时获取 token。
- 每条记录明确 owner、消费者、轮换周期、撤销方式与审计位置。轮换先验证新凭据和真实允许/拒绝结果，再切换消费者并撤销旧认证；覆盖或删除 KV 值本身不等于旧证书/token 已失效，历史 KV 版本也按受控保留/销毁规则处理。
- 验收必须覆盖指定记录可读、相邻角色及错误环境/集群记录拒绝、目标 UID 不符拒绝、过期凭据拒绝及审计记录。路径、policy、写入工具和实际记录均尚未实施，不把本节规划当作已有秘密或有效授权。

## 5. 通用权限矩阵

这是待审查的起始权限，不代表当前线上已生效。业务 namespace、控制面 namespace、资源名、API group 和动作全部来自目标固定声明；不得把任何生产服务名或 namespace 列表作为通用 role 的默认值。

| 操作 | root 管理主体 | operator 初期 | auditor |
| --- | --- | --- | --- |
| 集群/节点状态 | 已声明的管理所需读取 | 仅必要的集群状态读取 | 仅必要状态读取；不授予 `nodes/proxy` |
| Flux CD、GEO、Ingress 及基础组件配置 | 按明确管理范围更新 | 可读取经授权状态；配置变更拒绝 | 仅审查后的只读配置/状态 |
| namespace 中 Deployment/Service 状态 | 按管理职责 | 授权 namespace 的必要状态读取 | 仅经敏感性评审的状态；注意原始 Pod spec、事件和日志可能暴露敏感信息 |
| 常规无状态服务缩放 | 按需管理 | 仅被点名、经审查的无状态 Deployment 的 `deployments/scale` 操作；先在隔离环境验证 | 拒绝 |
| 镜像、配置和发布变更 | 审查管理声明 | 通过 GitOps 改动和已授权发布入口执行；验证实际 revision 和健康 | 拒绝 |
| workload 模板创建/更新 | 依明确管理范围 | 初期拒绝；准入限制和真实越权拒绝验收通过后再按服务放行 | 拒绝 |
| StatefulSet、PVC、PV、数据库/Vault 状态变更 | 仅受控管理/恢复阶段 | 初期拒绝；有状态版本/格式转换使用新集群 | 拒绝 |
| Secret、ServiceAccount token | 仅确需的管理或专用 owner | 直接 get/list/watch、签发 token 初期拒绝 | 拒绝 |
| exec/attach、节点 proxy、RBAC bind/escalate/impersonate | 管理主体按职责逐项审查 | 初期拒绝 | 拒绝 |
| RBAC、准入、CRD、namespace、集群管理 | 精确管理权限；高风险动作单独记录 | 拒绝 | 拒绝 |
| 自身授权范围的 GitOps 变更 | 审查发布三类身份授权 | 可提出需求；不能自行批准扩权或修改线上授权 | 可提出读取需求；不能自行批准扩权或修改线上授权 |

只读 `get/list/watch` 同样可能返回完整对象和敏感字段；不要把内置 `view` 直接等同安全审计。Kubernetes 原生 RBAC 不提供任意字段级脱敏，需要受控报告或专门的脱敏接口。

`operator` 的 `deployments/scale` 权限如果配置为具体资源名，应验证子资源授权实际按预期生效；临时缩放可能被 HPA 或 Flux 恢复，必须记录 owner 和恢复方式。此能力不覆盖有状态服务。

创建或修改 workload 可以间接挂载 Secret、使用其他 ServiceAccount 或通过高权限 Pod 接触节点，仅靠禁止直接读取 Secret 不足以限制提权。来源：[Kubernetes RBAC good practices](https://kubernetes.io/docs/concepts/security/rbac-good-practices/) 和 [Authorization: workload creation](https://kubernetes.io/docs/reference/access-authn-authz/authorization/)。

### 5.1 auditor：迁移与安全审计所需的只读范围

目标是“可以查看、不能改变系统或业务状态”。查看必要信息必须有真实权限；不能只创建一个无实际可见性的审计账户，也不能因为标为只读就默认授予所有原始内容。

`auditor` 同时用于业务系统迁移：在明确授权范围内完成源端资产/依赖盘点、配置与 SQL schema 核对、业务数据或文件的只读提取/导出，以及迁移前后完整性和一致性比较。迁移声明需明确源、目标、读取对象、导出位置、有效期及敏感数据处理要求；所需读取由对应数据库、文件或服务 owner 授权，不因 Kubernetes 只读身份自动获得。导出仅写入指定的迁移工作目录或制品位置，不改动源数据或目标业务存储。目标导入和业务写入由 `operator` 执行，初始化、DDL 或恢复等管理步骤由 `root` 执行；同一迁移流程按步骤显式选择身份，不给 auditor 附加写权限。

| 审计/迁移需求 | 必要读取能力 | 拒绝及保护条件 | 执行 owner |
| --- | --- | --- | --- |
| 确认目标及资源归属 | 明确集群/namespace 的节点、namespace、服务及所需资源状态 | 只授予声明中的 get/list/watch；不授予 nodes/proxy 或任何写入 | K3s/K8s role |
| 权限安全审查 | 读取授权范围内 Role、RoleBinding、ClusterRole、ClusterRoleBinding 及已审查准入策略定义 | 不允许修改/绑定角色、bind/escalate/impersonate、签发 token 或创建 CSR | K3s/K8s role |
| workload、存储与配置核对 | 查看迁移相关工作负载、PVC/PV 元数据、镜像版本、配置引用及存储关联的授权信息 | Deployment/Pod spec、ConfigMap、Events 等可能带敏感值；原始对象必须逐类审查，否则只通过脱敏报告读取。不得把子资源或 CLI 列展示当作字段隔离 | K3s/K8s role 与报告 owner |
| 日志、指标和故障分析 | 授权服务的脱敏日志、指标及运行报告 | 默认不授予任意 pods/log、原始 ConfigMap、全量监控数据或含敏感信息的非资源端点；无 exec/attach/port-forward/proxy | 观测/报告 owner；集群 role 校验调用边界 |
| 迁移源核对与导出 | 按数据库 owner 审查范围读取 schema/迁移版本、对象清单、代表性行、计数/摘要、业务差异及必要只读导出 | 不授予源写入、DDL、超级权限、危险函数或全库秘密读取；需要完整敏感载荷时使用专门受控导出流程，而不是扩大通用 auditor 权限 | 数据库/迁移 role |
| 迁移目标核验 | 以单独授权的只读目标连接检查已迁移对象和差异 | 不读取 operator 的目标写入凭据，不执行导入、修复、初始化、恢复或 schema 变更 | 数据库/迁移 role |
| OS 与文件安全分析 | 读取授权系统状态和受控报告，在自己的工作目录保存报告/授权导出 | 不读取 root 凭据、数据库数据目录、运行时 socket；不能写系统目录或目标业务存储 | vhosts/common 与报告 owner |

必要权限按源和目标分别明确，不因为同名 Linux 用户而继承数据库或 Kubernetes 管理权限。迁移任务参数必须显式选择 `root`、`operator` 或 `auditor`；`auditor` 只能执行只读检查、比较和授权导出。目标写入由 operator 执行，管理步骤切换 root；不能在 auditor 权限不足时自动提权。

Kubernetes RBAC 无原生拒绝规则，权限为各绑定授权的并集；必须核查实际所有绑定及聚合角色，而不只检查新角色。未允许动作应通过缺少授权和必要准入约束拒绝，不能添加一个“deny Role”后假定可覆盖旧权限。

审计身份明确不授予：Secret 的 get/list/watch、工作负载创建/更新/删除、exec、attach、port-forward、proxy、TokenRequest、CSR 创建/审批、RBAC 变更、bind、escalate 和 impersonate。平台自带仅用于检查自身权限的 API 如 SelfSubjectAccessReview 属于权限查询，需单独核实其有效默认权限；不能将该自省能力误写为可创建业务对象。

生产验收不得为证明拒绝而执行实际 DELETE、DDL 或导入。使用真实审计凭据、权限自省、无副作用请求和隔离样本验证；报告个人目录可写与系统/业务状态不可写分别记录。

## 6. 可更新能力的渐进交付

### 阶段 A：状态与受限维护

- 保留 Flux 作为线上资源声明 owner；operator 使用审查后的 GitOps PR、固定版本和受控发布入口更新应用。
- 先交付管理员维护的通用权限声明，再验证 operator 业务范围和 auditor 只读范围可通过 GitOps 独立更新、收敛与撤销；业务调谐不继承基础管理身份。
- 独立 RBAC 仅提供已声明 namespace 中的运行状态读取和 Deployment 缩放；不开放原始 Pod 创建、工作负载模板 patch、Secret、exec/attach、节点代理、RBAC 或任意 ServiceAccount 使用。OS operator 的默认 kubeconfig 只便利调用这套受限身份，不扩大授权。
- 测试 Flux 自动对账、HPA 与恢复行为。需要改变 SQL schema、数据库版本或持久状态时遵循新集群规则，不通过 operator 的临时 patch 原地升级。

### 阶段 B：确需直接创建或更新 workload 时

先在隔离环境实现并验证准入策略，约束操作者、namespace、资源和变更字段，包括镜像来源、ServiceAccount、卷/Secret、hostPath、容器特权、capabilities、宿主网络、runAsUser 和准入策略自身的修改权限。策略要覆盖 Deployment 等控制器模板与最终 Pod，并考虑 init/ephemeral containers 及 webhook/控制器重试。禁止操作员改写准入策略或更换未审核的服务身份。

准入不可用、策略缺失或绕过验证失败时停止放行 workload 创建/更新。策略应考虑节点、控制器和必要运维组件的实际兼容性；不是一次性将所有 namespace 强制改为同一安全等级。直改还须明确 Flux 回写、审计和版本来源，不以现场变更替代权威声明。

## 7. 执行顺序、验证与回退

| 阶段 | 内容 | 放行证据 |
| --- | --- | --- |
| 0 只读盘点 | 核实目标主机、集群 UID、K3s 版本、当前管理凭据、旧审计身份和现有消费者 | 实际对象、owner、访问路径与版本记录；未确认项标记 UNKNOWN |
| 1 声明与审查 | GitOps 定义目标、身份引用、RBAC、准入和验收范围；Playbooks 定义通用执行接口 | 多仓 PR、固定 SHA、对象所有权、干净工作树和回退范围 |
| 2 隔离验证 | 在隔离集群/namespace 用真实三身份执行允许、拒绝与重跑 | 认证与审计记录、负向验证、准入、幂等和恢复通过 |
| 3 目标集群创建身份 | 不撤销旧身份，先交付 auditor、operator，再验证受限 root 管理主体 | 三个不同认证主体与 kubeconfig、文件权限和登录成功；现有 root 恢复入口仍可用 |
| 4 目标集群权限验证 | 逐身份、逐 namespace 验证本矩阵 | 实际身份允许/拒绝结果与固定声明一致；生产负向测试使用安全授权查询或隔离对象，不执行真实破坏性操作 |
| 5 消费者迁移 | 将旧审计调用方和引导验证器逐项迁移到新身份 | 先替换 owner 和 caller，核实无旧消费者，记录拒绝证据 |
| 6 旧身份收尾 | 经核实后停用声明中列出的旧 Linux/Kubernetes 审计身份 | 仅清理已确认无消费者的对象；保留历史证据，不误删数据 |

Kubernetes 验收至少包含：

- `root`：真实管理主体可执行已声明管理操作；记录其有效权限；恢复入口仍可用。
- `operator`：在授权 namespace 使用允许的读取/缩放；跨 namespace、RBAC 修改、Secret、节点 proxy、exec 和未放行模板变更均被拒绝。
- `auditor`：以实际 SSH 用户和独立 Kubernetes 认证读取授权状态；创建/更新/删除、Secret、exec/attach/proxy、TokenRequest、impersonate、bind/escalate 均拒绝。
- GitOps 动态更新：管理员发布授权变更后，验证 operator 的业务范围及 auditor 的只读范围按 revision 生效；收缩授权后原范围不可访问。operator/auditor 不能自行修改 RBAC、基础调谐配置或执行身份；审计配置更新不引入业务写权限。
- 数据权限与服务账号：避免通过 Pod、模板、准入例外、继承角色和聚合角色间接越权；核对当前 RBAC 实际组合。
- 重跑与撤销：相同固定候选幂等；撤销一个身份不影响其他身份；凭据到期/失效后认证被拒绝。

记录 PASS/FAIL/BLOCKED/NOT_RUN，包含时间、目标、SHA、真实身份、预期和脱敏结果。管理员 impersonation 只能作为辅助，不替代真实凭据测试。

失败时停止后续阶段，保留旧 root 与审计消费者；按已审查声明和对应证据撤回新增绑定、撤销新增凭据。不能因身份修复删除 PVC、重建集群、修改来源环境或切换 DNS；不将部署失败后的数据回滚与代码回退混为一谈。

## 8. 当前未决项

- ❌ 独立 Kubernetes 认证机制、颁发/轮换 owner 和凭据交付方式尚未确定或实测。
- ❌ `kv/<environment>/<cluster>/{root,operator,auditor}` 的记录、精确 Vault policy、凭据写入/轮换及跨角色拒绝验收尚未实施。
- ❌ root 管理 Flux CD、GEO、Ingress 等基础组件的准确 API 权限及高风险操作范围尚需核定；GEO 的具体组件含义待确认。
- ❌ operator 可操作的准确 namespace、对象名、维护动作及 GitOps 发布入口尚需逐项核定。
- ❌ 直接 workload 变更所需准入策略和兼容性尚未实现、验收或放行。
- ❌ auditor 实际读取清单、脱敏方式及允许/拒绝证据尚未完成。
- ❌ GitOps 授权单元、受限业务调谐身份、仓库审查规则及动态授权/撤销验收尚未落地。
- ❌ 目标遗留审计身份的全部调用方与迁移完成条件尚未实时盘点；不得先删除。
