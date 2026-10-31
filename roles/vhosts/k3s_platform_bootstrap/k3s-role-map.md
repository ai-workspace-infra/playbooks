# K3s role 职责与依赖清单

## 当前主线与依赖

```text
IaC CMDB + 本次解析到 commit 的 GitOps 期望配置
  → 入口绑定目标
  → common：hostname、OS 三账户/组、主机基线
  → web_saas_data_volume：独立数据盘文件系统与挂载证据
  → k3s_platform_bootstrap（只编排）
      → flux_gitops / k3s_identity：输入预检
      → k3s-cluster-server：控制面初始化及 API 就绪
      → helm_cli：Helm 客户端
      → flux_controllers：Flux 控制器及 rollout
      → flux_gitops：source/root 交接及独立回执
      → k3s_identity：三套认证凭据交付及真实允许/拒绝
  → Flux 按 GitOps 依赖调谐 RBAC、ESO/TLS、Vault、DB、监控和业务资源
```

RBAC 必须先就绪才能执行真实身份权限验收；不能让它反向依赖尚未签发的
三身份凭据，也不能用“管理员 impersonation 成功”替代真实身份登录。
主机名、Flux root 名、环境批次目录和 source path 各自独立，不按名字推断流程。

## 当前相关 role 列表（2026-10-31）

以下状态区分仓库 `main`、待合并候选和生产实际验收。Role 存在或 CI 通过不等于目标集群已完成部署。

| 项目 | 当前状态 | 当前责任与缺口 |
| --- | --- | --- |
| `k3s_platform_bootstrap` | ✅ Playbooks #903 已合并 | 只编排子 role 及依赖顺序 |
| `k3s-cluster-server` | ✅ #903 已合并 | K3s 控制面初始化、hostname 校验、API 就绪；不承担插件发布 |
| `k3s-cluster` | ✅ #903 已删除旧实现 | 两个安装入口已切换到 `k3s-cluster-server` |
| `helm_cli`、`flux_controllers`、`flux_gitops`、`k3s_identity` | ✅ #903 已合并 | 分别负责 Helm 客户端、Flux 控制器、声明交接、三身份交付和验证；真实身份、Vault writer 与 RBAC 仍需目标集群验收 |
| `k3s` 旧安装器 | ✅ [Playbooks #905](https://github.com/ai-workspace-infra/playbooks/pull/905) 已合并并移除旧实现 | 安装由 server 负责；非默认 CNI 明确拒绝，需另行交付独立的 CNI 能力 |
| `k3s_platform_addon`、`k3s-addon` | ❌ 旧实现和调用方仍存在 | 待目标环境采用 Flux 声明并完成发布身份、调用方和运行验证后退役，当前不属于新 bootstrap 主线 |
| cert-manager、reloader、APISIX、ExternalDNS、NGINX Ingress | ✅ [GitOps #681](https://github.com/ai-workspace-infra/gitops/pull/681) 已合并 | 已提供可独立选择的 Flux 模板，未接入任何环境；ExternalDNS 默认 dry-run |
| ESO、Caddy | ✅ GitOps 已有 V6 HelmRelease 声明；❌ 实际调谐及服务验收待核验 | 不再通过旧 addon role 重复安装 |
| `k3s-reset` | ❌ 安全边界待修复 | 独立破坏性入口，旧默认值需收敛；#905 移除两个初始化入口的隐式重置 |
| `k3s_runtime_secret_sync` | ✅ 独立运维入口 | 不属于 bootstrap；通用 KV 同步与环境专用投影仍需梳理 |
| 三身份与 Flux RBAC | ❌ [GitOps #682](https://github.com/ai-workspace-infra/gitops/pull/682)、[Playbooks #906](https://github.com/ai-workspace-infra/playbooks/pull/906) 待合并 | 候选声明三身份和 RBAC 依赖；operator 当前只具备业务读取及扩缩容，工作负载写入需另审准入策略 |
| Orchestrator → Playbooks | ❌ 参数交接未贯通 | 需传入 GitOps checkout/SHA、资源声明路径、CMDB SHA 和回执路径 |
| Vault writer 与真实身份验收 | ❌ 未完成 | 使用受控短期身份写入三个精确路径后，以实际账户验证认证及允许/拒绝 |
| V6 Flux 接管和服务部署 | ❌ 尚无完整运行验收证据 | 需核对调用链、身份授权、实际 RBAC 和服务就绪；不得据源码或静态测试宣称完成 |

当前清单文件迁入本目录的 [Playbooks #904](https://github.com/ai-workspace-infra/playbooks/pull/904) 已合并。所有角色遵循 `AGENTS.md` 的单一职责规则。

## 主线执行 role

| Role | 唯一能力 | 前置条件 | 输出/验收 |
| --- | --- | --- | --- |
| `helm_cli` | Helm 客户端准备 | 主机包/CA/网络 | 现有客户端或安装成功，不部署 chart |
| `flux_controllers` | Flux CLI 与控制器初始化 | K3s API、私有 admin kubeconfig | controller rollout 就绪 |
| `flux_gitops` | 经审查的 source/root 交接 | 解析过 commit 的 GitOps 配置与 ready Flux | Flux 交接回执，不声称业务 Ready |
| `k3s_identity` | Kubernetes 三身份生命周期 | OS 三账户、实际 CA/UID、GitOps RBAC、受控 Vault writer | 独立密钥/证书/私有 kubeconfig、KV CAS 归档、真实认证与允许/拒绝 |

生成器、模板、默认值及验收脚本随能力移动；没有复制第二套实现。
现有 `k3s_platform_*` 和 `k3s_identity_*` 为调用合同，未增加环境专用别名。

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
| `k3s_platform_addon` | 直接 Helm 管理 cert-manager/ESO/reloader/Caddy/APISIX/external-dns，默认含业务路由与域名；与 GitOps 资源 owner 重叠 | 本次不接入冷启动主线，也未删除；后续逐资源确认 GitOps 接管及旧消费者退出，停止双重调谐，再清理实现 |
| `k3s-addon` | 脚本管理 ingress/DNS，meta 自动安装 cert-manager，存在忽略错误与销毁操作 | 存量旧入口冻结；不并入控制面 role。核对旧调用方及实际资源后迁入 GitOps/对应 owner，禁止新主线使用 |
| `k3s-reset` | 调用重置脚本，旧默认 `cluster_reset=enable`；属于破坏性恢复 | 独立保留，不列入初始化依赖。本次未执行；显式确认、精确范围、备份和恢复验证等防护仍需另行收敛 |
| `k3s_runtime_secret_sync` | Vault KV 字段 CAS 同步，并包含 dev-registry 投影模式；不是 K3s 安装/RBAC | 保留独立运维入口，不进入 bootstrap。后续拆分通用 KV 同步与环境投影；不能自动扩大路径权限或覆盖业务记录 |

这些历史 role 的问题是盘点结论；本次未对所有旧实现逐一改造，不能将
“本次主线不调用”写成“全仓陈旧代码已清理”。

### 入口与历史调用迁移

| 入口 | 调用路径 | 范围 |
| --- | --- | --- |
| `k3s_platform_bootstrap.yml` / `scripts/pipeline/bootstrap-k3s.sh` | CMDB → common → 独立盘 → bootstrap 子 role DAG | 正常 IaC 交付 |
| `k3s_platform_bootstrap_with_gitops.yml` | bootstrap 子 role DAG | 调用方先完成 common/存储及非敏感输入绑定 |
| `k3s_identity_bootstrap.yml` | 直接 `k3s_identity` | 已有 master 的独立身份初始化，不重装集群 |
| `k3s-cluster.yaml`、`setup-k3s-node.yaml`、`init_k3s_cluster_server` | 直接 `k3s-cluster-server` | 仅初始化控制面，目标组统一 `k3s`；不再隐式部署 Registry/Flux/ESO |
| `k3s_platform_addon.yml`、`k3s_reset.yml`、`reconcile_k3s_runtime_secrets.yml` | 保持独立旧入口 | 非新 bootstrap 的依赖，不自动执行 |

空的 worker role 和入口已移除。独立脚本模拟安装 Flux/ESO 不是本 DAG 验收；
OrbStack 历史模拟探针不构成拆分后调用链或幂等性的证据。

旧 Dev reusable workflow 仍固定 checkout 历史 Playbooks SHA，不会自动采用本次
拆分；其专用 adapter 仍依赖旧 source/root 默认值且未提供完整身份合同。
它不属于已接通的新入口，更新该消费者前需从所选 GitOps 声明补齐配置与
common 前置，不能仅移动 checkout SHA 或据其旧回执宣称本次部署通过。
