# K3s role 职责与依赖清单

## 当前主线与依赖

```text
IaC CMDB + 本次解析到 commit 的 GitOps 期望配置
  → 入口绑定目标
  → common：hostname、OS 三账户/组，并调用 web_saas_data_volume 准备独立数据盘
  → k3s_platform_bootstrap（只编排）
      → flux_gitops / k3s_identity：输入预检
      → k3s-cluster-server：控制面初始化及 API 就绪
      → helm_cli：Helm 客户端
      → flux_controllers：Flux 控制器及 rollout
      → flux_gitops：source/root 交接及独立回执
      → k3s_identity：三套认证凭据交付及真实允许/拒绝
      → flux_workload_readiness：声明的 source、Kustomization 和 HelmRelease 就绪证据
  → Flux 按 GitOps 依赖调谐 RBAC、ESO/TLS、Vault、DB、监控和业务资源
```

RBAC 必须先就绪才能执行真实身份权限验收；不能让它反向依赖尚未签发的
三身份凭据，也不能用“管理员 impersonation 成功”替代真实身份登录。
主机名、Flux root 名、环境批次目录和 source path 各自独立，不按名字推断流程。

## 当前相关 role 列表（2026-10-31）

以下状态区分仓库 `main`、待合并候选和生产实际验收。Role 存在或 CI 通过不等于目标集群已完成部署。

| 项目 | 当前状态 | 当前责任与缺口 |
| --- | --- | --- |
| `vhosts/common` 与独立数据盘 | ✅ [Playbooks #916](https://github.com/ai-workspace-infra/playbooks/pull/916) 已合并；❌ 目标节点盘就绪待验收 | 通用 K3s 入口由 common 调用数据盘 role，挂载及 UUID 验证通过后才发出 common 回执；此前节点的 100 GB 盘未挂载、无文件系统，需实际执行后重新核验 |
| `k3s_platform_bootstrap` | ✅ Playbooks #903 已合并 | 只编排子 role 及依赖顺序 |
| `k3s-cluster-server` | ✅ #903 已合并 | K3s 控制面初始化、hostname 校验、API 就绪；不承担插件发布 |
| `k3s-cluster` | ✅ #903 已删除旧实现 | 两个安装入口已切换到 `k3s-cluster-server` |
| `helm_cli`、`flux_controllers`、`flux_gitops`、`k3s_identity` | ✅ #903 已合并 | 分别负责 Helm 客户端、Flux 控制器、声明交接、三身份交付和验证；真实身份、Vault writer 与 RBAC 仍需目标集群验收 |
| OS `operator` 的默认 K3s 客户端身份 | ❌ Playbooks 候选待合并 | `k3s_identity` 候选将 `~/.kube/config` 安全链接到 OS operator 自己的受限 `identity-config`，拒绝覆盖不受管配置，并验证默认 `kubectl` 主体；不授予 K3s admin kubeconfig、containerd socket 或额外 sudo |
| `k3s` 旧安装器 | ✅ [Playbooks #905](https://github.com/ai-workspace-infra/playbooks/pull/905) 已合并并移除旧实现 | 安装由 server 负责；非默认 CNI 明确拒绝，需另行交付独立的 CNI 能力 |
| `k3s_platform_addon`、`k3s-addon` | ✅ 已从当前代码及调用方移除 | 平台插件由 GitOps 按环境显式选择；现有发布的 Flux 接管与运行验收仍需逐项验证 |
| cert-manager、reloader、APISIX、ExternalDNS、NGINX Ingress | ✅ [GitOps #681](https://github.com/ai-workspace-infra/gitops/pull/681) 已合并 | 已提供可独立选择的 Flux 模板，未接入任何环境；ExternalDNS 默认 dry-run |
| ESO、Caddy | ✅ GitOps 已有 V6 HelmRelease 声明；❌ 实际调谐及服务验收待核验 | 不再通过旧 addon role 重复安装 |
| `k3s-reset` | ❌ 安全边界待修复；[Playbooks #915](https://github.com/ai-workspace-infra/playbooks/pull/915) 待合并 | 空节点无操作修复已在目标节点验证；已安装集群的重置仍需单独验证，不属于初始化依赖 |
| `k3s_runtime_secret_sync` | ✅ 独立运维入口 | 不属于 bootstrap；通用 KV 同步与环境专用投影仍需梳理 |
| 三身份与 Flux RBAC | ✅ [GitOps #682](https://github.com/ai-workspace-infra/gitops/pull/682)、[Playbooks #906](https://github.com/ai-workspace-infra/playbooks/pull/906) 已合并；⚠️ GitOps [#686](https://github.com/ai-workspace-infra/gitops/pull/686) 扩展 operator 状态读取待审；❌ 目标集群验收待执行 | 当前声明允许业务 namespace 的 Service/Deployment/HelmRelease 读取和 Deployment scale；#686 候选增加 Pods/ReplicaSets/Events 状态读取，并明确拒绝 pods/log、Secret、跨 namespace 和工作负载 spec 写入。K3s operator 仍是业务 namespace 级，不是节点或集群管理员 |
| Orchestrator → IaC CMDB → Playbooks | ⚠️ 参数选择与调用入口已合并；生产端到端执行未验收 | [Orchestrator #1585](https://github.com/ai-workspace-infra/Platform-Orchestrator/pull/1585) 已合并：根据 IaC CMDB catalog 精确选择目标声明，并支持从 GitOps host vars 解析 K3s bootstrap reader、SSH Vault 字段与三身份 writer 参数；reader 所需字段目前仍只在 GitOps [#684](https://github.com/ai-workspace-infra/gitops/pull/684) OPEN 候选中。Kubernetes-only Web SaaS 路径跳过传统原生应用凭据准备。最近 apply [#41187083044](https://github.com/ai-workspace-infra/Platform-Orchestrator/actions/runs/41187083044) 使用合并前版本，在旧 Vault `ref` claim 处停止，common、数据盘、K3s/Flux 均未运行；配置合并和 Vault reader 实际应用后，需从当前 main 重新验收完整调用链 |
| Vault writer 与真实身份验收 | ❌ 运行验收未完成；[Playbooks #918](https://github.com/ai-workspace-infra/playbooks/pull/918)、[GitOps #683](https://github.com/ai-workspace-infra/gitops/pull/683) 已合并 | K3s identity writer policy/role 已定向应用，精确授权三条 create/read 路径并绑定受保护 PROD Deploy 身份；该身份负责签发凭据归档，不负责节点 SSH 登录或 common 公钥读取。真实 JWT 登录、相邻路径拒绝和三账户 RBAC 尚待验证 |
| common Vault 公钥 reader | ❌ 尚未应用/运行验收；GitOps [#684](https://github.com/ai-workspace-infra/gitops/pull/684) OPEN，Playbooks [#920](https://github.com/ai-workspace-infra/playbooks/pull/920) 已合并 | 单独的只读 identity 只读取三条 OS 公钥记录；common 通过严格 HTTPS/TLS 的 Vault KV v2 API 消费短期 token，不依赖 runner 安装 Vault CLI。#684 合并后还需由受控管理员定向配置 role/policy，再验证允许目标路径、拒绝相邻路径 |
| Verify 阶段前置条件 | ❌ [Orchestrator #1587](https://github.com/ai-workspace-infra/Platform-Orchestrator/pull/1587) OPEN | 明确要求 Resolve、IaC/Provision、Deploy 成功后才执行 Verify；合入前不应以不完整 stage 结果宣称全链路验收 |
| Flux 接管和服务部署 | ❌ 尚无完整运行验收证据 | 最近 apply 在 Provision 阶段失败，Deploy/Migrate/Verify 均跳过；没有 common、数据盘、OS/K3s 身份、K3s/Flux、Vault、PostgreSQL、Observability、Web SaaS、Ingress 的本次运行就绪证据。需先满足 Vault reader 与 Verify 门禁，再按当前 main 重派并记录真实回执 |

当前清单文件迁入本目录的 [Playbooks #904](https://github.com/ai-workspace-infra/playbooks/pull/904) 已合并。所有角色遵循 `AGENTS.md` 的单一职责规则。

## 主线执行 role

| Role | 唯一能力 | 前置条件 | 输出/验收 |
| --- | --- | --- | --- |
| `vhosts/common` 的 `node_bootstrap.yml` | 校验节点身份、建立 OS 三账户并串联声明的独立盘准备 | CMDB 精确节点、受控 SSH 公钥；既有节点和 PV 亲和性检查 | 主线要求盘挂载及 UUID 证据后才产生 `COMMON_NODE_VERIFIED`；不代表完整主机安全基线 |
| `vhosts/common` 的 OS Vault reader | 以受控 Vault 会话读取三账户公钥 | 明确的 HTTPS Vault、目标环境和只读会话；证书严格校验；分别依赖 GitOps reader 声明和精确只读 Vault role | #920 候选使用 KV v2 HTTPS 接口，无需在 runner 安装 Vault CLI；缺失或拒绝读取时失败；目前没有 V6 实际调用回执 |
| `web_saas_data_volume` | 核验并准备声明的独立文件系统 | CMDB 实例、卷及设备匹配；空盘初始化需显式授权 | 挂载点、UUID、文件系统证据；不自动重格式化已有文件系统 |
| `k3s-cluster-server` | 初始化单节点 K3s 控制面 | common 已设置 hostname；要求独立盘时卷证据已通过 | K3s 服务启动及 API `/readyz`；既有安装跳过安装器，不等于已升级 |
| `k3s_platform_bootstrap` | 按依赖顺序编排子 role | 已完成入口绑定、common 和数据盘角色 | 子 role 顺序执行；失败停止后续执行，不代表业务服务验收 |
| `helm_cli` | Helm 客户端准备 | 主机包/CA/网络 | 现有客户端或安装成功，不部署 chart |
| `flux_controllers` | Flux CLI 与控制器初始化 | K3s API、私有 admin kubeconfig | controller rollout 就绪 |
| `flux_gitops` | 经审查的 source/root 交接 | 解析过 commit 的 GitOps 配置与 ready Flux | Flux 交接回执，不声称业务 Ready |
| `k3s_identity` | Kubernetes 三身份生命周期 | OS 三账户、实际 CA/UID、GitOps RBAC、受控 Vault writer | 独立密钥/证书/私有 kubeconfig、KV CAS 归档、真实认证与允许/拒绝 |
| `flux_workload_readiness` | 只读观察声明的 Flux 对象 | 三身份任务完成且 GitOps 提供待观察对象 | 独立 `FLUX_OBJECTS_READY` 回执；记录各对象 revision，不替代应用接口、数据和用户流程验收；候选随 #918，未生产验证 |

生成器、模板、默认值及验收脚本随能力移动；没有复制第二套实现。
现有 `k3s_platform_*` 和 `k3s_identity_*` 为调用合同，未增加环境专用别名。

## 输入、证据与重复执行边界

| 阶段 | 必要输入与失败条件 | 可记录证据和重复执行边界 |
| --- | --- | --- |
| 入口选择 | `MATRIX_HOST`、CMDB 文件及 SHA、固定 GitOps checkout/SHA、仓库内资源声明路径和新的回执路径；目标/区域/实例/盘不匹配即失败 | 本次 GitOps/Playbooks SHA、CMDB SHA 和实例 ID；当前入口仍校验 Linode 字段，跨 provider 不可假定已支持 |
| common | 节点名称、OS 身份和声明的独立盘输入；已有节点/PV 亲和性冲突或磁盘未就绪即停止 | 主线的 `COMMON_NODE_VERIFIED` 包含节点、OS 身份、实例 ID 和挂载证据；不表示 fail2ban、auditd、UFW 或云防火墙通过 |
| 独立盘 | 精确卷 ID、设备、挂载目标及文件系统；他处挂载、未知签名或目标已有文件时停止 | 记录真实 UUID 和挂载结果；已挂载时核对同一设备并保留数据；仅无签名空盘可在显式授权下初始化 |
| 控制面及 Flux | hostname、K3s 版本、私有管理 kubeconfig、声明的 source/root/path；声明或实际节点/版本/磁盘不匹配时失败 | `FLUX_HANDOVER_VERIFIED` 记录 source/applied revision 与集群和节点标记；不表示 RBAC、身份或业务就绪。当前 active 验证器约束单节点及 ext4 |
| 三身份 | 声明的 RBAC Kustomization 当前 generation Ready 且应用所选 commit；独立短期 Vault writer、实际 CA、UID 和 allow/deny 清单 | master 签发独立凭据、验证信任链并以 KV v2 CAS=0 写入，CA 私钥留在 master；不同记录、部分签发或过期证书失败，不以删除数据强制重试 |
| 服务与 DNS | GitOps 依赖图、服务所需的受控凭据与制品、目标环境声明 | 分别记录各服务健康、接口和数据证据；DNS 变更必须符合单独声明与授权，Flux 交接回执不是流量切换或服务验收 |

**回执时序：** 当前 `flux_gitops` 在 `k3s_identity` 运行之前写入交接回执。
身份任务失败时该文件仍可能存在；消费者必须同时检查整个 playbook/owner 进程退出状态及真实身份验收，不能仅凭文件或 `FLUX_HANDOVER_VERIFIED` 判定整条主线成功。

入口的身份字段断言在 common 和数据盘之前；`flux_gitops` 与 `k3s_identity` 的完整 role 预检则在两者之后。因而缺少某些 Flux 输入时，不能声称所有主机或数据盘操作都尚未发生；应先在 caller 补足前置检查。

匹配已有挂载和受保护的身份状态可支持部分重试；当前没有目标节点的完整重复运行证据。数据库版本、SQL schema、Vault Raft、监控存储及其他持久卷的升级或迁移仍按新集群和独立存储处理；代码或回执的回退不等于数据回滚。

## 验证和发布边界

本次同步受影响的 Python 包装器、测试导入、Ansible 入口和 CI 路径过滤。
契约测试检查编排器没有直接副作用、独立 role 可解析、前置失败不会进入
安装，以及真实身份生成器的证书/目标绑定/CAS 行为。生产验证必须另记
实际 GitOps/Playbooks SHA、CMDB 实例、运行 ID 与真实身份/服务结果。

本次已合并的拆分未执行卸载、原地升级、删盘、数据迁移、DNS 更新或凭据轮换。
回退 PR 只恢复代码调用路径，不回滚数据，也不撤销已签发证书。

## 历史 role 与迁移记录

以下表格记录 #903 完成时的旧实现与处理；后续变化以文首状态表和对应 PR 为准。

| 原 role | 实际职责/问题 | 本次处理与后续边界 |
| --- | --- | --- |
| `k3s_platform_bootstrap` | 原来混合 K3s、Helm、Flux、交接、凭据生成与验收；README 含暂停式旧描述 | 改为仅调用子 role 的编排器；执行实现全部归对应子 role |
| `k3s-cluster-server` | 原 bootstrap 同时安装 K3s/Flux/ESO，写业务 namespace/Vault token Secret；静态 vars 写死目标；其余五个 action 文件为空 | 保留为唯一主线控制面安装 role；替换旧混合 bootstrap，删除空 action、静态 vars、无调用模板与旧生成脚本。只实现初始化，非 bootstrap 的 action 明确拒绝 |
| `k3s-cluster` | 脚本安装 K3s，随后隐式装 nerdctl/CNI、本地 Registry 和自签 CA；重复安装且夹带额外资源 | 合并控制面能力到 `k3s-cluster-server`；更新两个 playbook 调用方后删除目录。新主线不隐式创建 Registry；此行为变化必须在审查中确认 |
| `k3s` | 旧脚本混合 K3s/Helm/CNI；meta 自动依赖 common；含固定域名和忽略错误 | K3s 安装统一到 server，调用方切换，旧实现删除。非默认 CNI 明确拒绝，需由独立 GitOps 声明与执行链另行交付 |
| `k3s_platform_addon` | 直接 Helm 管理 cert-manager/ESO/reloader/Caddy/APISIX/external-dns，默认含业务路由与域名；与 GitOps 资源 owner 重叠 | 旧实现及入口已移除；环境按需选择 Flux 模板，现有发布的接管另行验证 |
| `k3s-addon` | 脚本管理 ingress/DNS，meta 自动安装 cert-manager，存在忽略错误与销毁操作 | 旧实现及调用方已移除；Ingress 和 DNS 由经审查的 GitOps/IaC 交付 |
| `k3s-reset` | 调用重置脚本，旧默认 `cluster_reset=enable`；属于破坏性恢复 | 独立保留，不列入初始化依赖。本次未执行；显式确认、精确范围、备份和恢复验证等防护仍需另行收敛 |
| `k3s_runtime_secret_sync` | Vault KV 字段 CAS 同步，并包含 dev-registry 投影模式；不是 K3s 安装/RBAC | 保留独立运维入口，不进入 bootstrap。后续拆分通用 KV 同步与环境投影；不能自动扩大路径权限或覆盖业务记录 |

这些历史 role 的问题是盘点结论；本次未对所有旧实现逐一改造，不能将
“本次主线不调用”写成“全仓陈旧代码已清理”。

### 入口与历史调用迁移

| 入口 | 调用路径 | 范围 |
| --- | --- | --- |
| `k3s_platform_bootstrap.yml` / `scripts/pipeline/bootstrap-k3s.sh` | CMDB → common（调用独立盘 role）→ bootstrap 子 role DAG | 正常 IaC 交付 |
| `k3s_platform_bootstrap_with_gitops.yml` | bootstrap 子 role DAG | 调用方先完成 common/存储及非敏感输入绑定 |
| `k3s_identity_bootstrap.yml` | 直接 `k3s_identity` | 已有 master 的独立身份初始化，不重装集群 |
| `k3s-cluster.yaml`、`setup-k3s-node.yaml`、`init_k3s_cluster_server` | 直接 `k3s-cluster-server` | 仅初始化控制面，目标组统一 `k3s`；不再隐式部署 Registry/Flux/ESO |
| `k3s_reset.yml`、`reconcile_k3s_runtime_secrets.yml` | 保持独立旧入口 | 非新 bootstrap 的依赖，不自动执行 |

空的 worker role 和入口已移除。独立脚本模拟安装 Flux/ESO 不是本 DAG 验收；
OrbStack 历史模拟探针不构成拆分后调用链或幂等性的证据。

旧 Dev reusable workflow 仍固定 checkout 历史 Playbooks SHA，不会自动采用本次
拆分；其专用 adapter 仍依赖旧 source/root 默认值且未提供完整身份合同。
它不属于已接通的新入口，更新该消费者前需从所选 GitOps 声明补齐配置与
common 前置，不能仅移动 checkout SHA 或据其旧回执宣称本次部署通过。
