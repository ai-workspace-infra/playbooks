# AI Workspace Runtime 交付计划

## 1. 目标与边界

本计划定义 AI Workspace 核心运行时从源码仓库构建、发布、离线聚合到目标机部署的完整交付链路。

核心原则：

- LiteLLM、xworkspace-console、xworkmate-bridge、QMD 分别在各自源码仓库的 GitHub Actions build job 中构建。
- 每个组件独立发布 `runtime-*` GitHub Release 及其 SHA256 清单。
- offline package 只下载已发布产物，逐文件完成 SHA256 校验后再聚合。
- 目标机只允许校验、解包、安装、配置、启动和健康检查，禁止源码编译、依赖构建及镜像构建。
- 所有未经过 CI 或目标机矩阵实测的能力均保持 `TODO`，不得仅依据设计或局部实现标记完成。

## 2. 目标架构

```text
LiteLLM repository ---------- build job --> runtime-litellm-* ----------\
xworkspace-console repository build job --> runtime-xworkspace-console-* --\
xworkmate-bridge repository -- build job --> runtime-xworkmate-bridge-* -----+--> offline package job
QMD repository -------------- build job --> runtime-qmd-* ------------------/      |
                                                                                   | download
                                                                                   | SHA256 verify
                                                                                   | manifest aggregate
                                                                                   v
                                                                        offline-package-*
                                                                                   |
                                                                                   v
                                                                  target host: verify/install only
```

### 2.1 组件 Release

四个组件必须由各自仓库负责构建，聚合仓库不得从源码代建组件。

| 组件 | 构建责任 | Release 命名 | 必需产物 |
| --- | --- | --- | --- |
| LiteLLM | LiteLLM 仓库 GitHub Actions build job | `runtime-litellm-*` | 固定版本 Python runtime/依赖包、启动入口、组件 manifest、SHA256 清单 |
| xworkspace-console | xworkspace-console 仓库 GitHub Actions build job | `runtime-xworkspace-console-*` | dashboard 静态产物、API 二进制、运行配置模板、组件 manifest、SHA256 清单 |
| xworkmate-bridge | xworkmate-bridge 仓库 GitHub Actions build job | `runtime-xworkmate-bridge-*` | bridge 二进制、systemd/运行配置模板、组件 manifest、SHA256 清单 |
| QMD | QMD 仓库 GitHub Actions build job | `runtime-qmd-*` | 已安装依赖和已构建 CLI/runtime、组件 manifest、SHA256 清单 |

每个组件 manifest 至少记录：组件名、源码 commit、版本、构建时间、目标 OS、目标架构、入口文件、文件列表及每个文件的 SHA256。

### 2.2 offline package 聚合

offline package job 必须：

1. 从四个组件的 `runtime-*` Release 下载与目标平台匹配的产物和 SHA256 清单。
2. 在聚合前执行 SHA256 校验；缺少清单、文件缺失或摘要不一致时立即失败。
3. 生成聚合 manifest，固定四个组件的 Release tag、源码 commit、资产 URL、资产大小和 SHA256。
4. 将已校验组件产物、部署 playbook 所需依赖及聚合 manifest 打包为 `offline-package-*`。
5. 对最终 offline package 再生成 SHA256，并在 CI 中执行一次解包与结构校验。

禁止以 `latest` 作为不可追溯的部署输入；重新聚合必须基于明确 tag 或不可变 commit。

### 2.3 目标机部署

目标机部署必须开启 prebuilt-only 约束。缺少任一预构建产物时直接失败，不得回退到以下行为：

- `git clone` 或源码 checkout；
- `npm install`、`npm run build`、`go build`、`go run`；
- `pip install` 从公网或源码解析构建依赖；
- `docker build`、`podman build` 或其他本地镜像构建；
- 任何需要编译器、SDK 或前端构建工具链的安装步骤。

部署仅执行：offline package SHA256 校验、manifest 校验、解包、文件安装、权限设置、配置渲染、服务启动、健康检查和结果汇总。

## 3. 资源与性能约束

### 3.1 并发控制

- 全局并发硬上限必须满足 `并发数 <= 2 * 在线 CPU 数`，在线 CPU 数以执行时实际可用 CPU 为准。
- 初始并发取任务上限、配置上限和 `2 * 在线 CPU 数` 三者最小值。
- 调度器必须随 load 动态收缩：负载超过阈值时停止发放新任务并逐级降低并发；负载恢复且持续稳定后再缓慢扩容。
- 动态收缩不得中断正在执行的不可重入安装步骤；只限制后续任务进入。
- 日志和最终摘要必须记录 CPU 数、load 采样、每次并发调整的时间、原因及调整前后值。

### 3.2 部署耗时分布

每次部署必须记录总耗时及至少以下阶段耗时：

- offline package 下载；
- SHA256 与 manifest 校验；
- 解包；
- 各组件安装；
- 配置渲染；
- 服务启动；
- 健康检查。

CI/验收报告按 OS、架构、冷启动/缓存命中、首次执行/幂等重跑分组，统计样本数、最小值、最大值、平均值以及 P50、P90、P95、P99。样本不足时保留原始数据并明确标注，不以单次耗时代替分布结论。

## 4. 支持矩阵与验收

目标支持以下全部组合：

| 发行版 | 版本 | 架构 |
| --- | --- | --- |
| Debian | 11、12、13 | amd64、arm64 |
| Ubuntu | 22.04、24.04、26.04 | amd64、arm64 |

每个矩阵项必须验证：

1. offline package 下载和 SHA256 校验成功。
2. 目标机在无源码、无构建工具链、组件外网访问受限的条件下部署成功。
3. 四个组件版本与聚合 manifest 完全一致。
4. 服务启动、健康检查和关键 smoke test 成功。
5. 同一主机使用同一输入至少连续执行两次；第二次成功且无非预期变更、无重复资源、无凭据轮换、无构建行为。
6. 首次部署和幂等重跑均产出阶段耗时及完整摘要。

Ubuntu 26.04 在实际可用 runner/镜像和依赖生态完成验证前，只能保持计划支持状态，不得标记已验证。

## 5. 当前事实

以下状态只记录当前仓库或相邻交付文档能够证明的事实，不把目标设计视为完成：

- [x] 聚合入口已拆分为 preflight 与 runtime playbook；preflight 已校验 `docker`、`k3s`、`systemd` 运行模式组合。
- [x] xworkspace-console 与 QMD 的部署代码已出现预构建 archive 输入及 prebuilt-only 缺包失败入口。
- [x] 相邻一键部署文档已记录：xworkspace-console 离线包 `publish-release` 链路和 Release 产物上传曾核对完成。
- [x] 相邻一键部署文档已记录：一键安装脚本优先使用离线安装包。
- [ ] xworkspace-console 与 QMD 当前仍存在目标机源码 checkout/依赖安装/构建回退，尚未满足“目标机禁止构建”。
- [ ] LiteLLM 当前可覆盖 package spec，但未证明其独立 `runtime-litellm-*` Release 和完全离线、免构建安装链路。
- [ ] xworkmate-bridge 独立 `runtime-xworkmate-bridge-*` Release 和预构建消费链路尚未在本计划范围内验证。
- [ ] 四组件 Release 的一致命名、manifest 和 SHA256 契约尚未完成验证。
- [ ] offline package 的逐文件下载、SHA256 校验、聚合 manifest 和最终包校验尚未完成验证。
- [ ] 并发硬上限、基于 load 的动态收缩及调整日志尚未完成验证。
- [ ] 部署耗时分布统计尚未完成验证。
- [ ] 连续重复执行的幂等性验收尚未完成。
- [ ] Debian 11/12/13、Ubuntu 22.04/24.04/26.04 的 amd64/arm64 全矩阵尚未完成验证。

## 6. TODO

### P0：构建与发布闭环

- [ ] TODO：在 LiteLLM 仓库建立 build job，发布 `runtime-litellm-*` Release、组件 manifest 和 SHA256 清单。
- [ ] TODO：在 xworkspace-console 仓库固化 build job，确认每次发布 `runtime-xworkspace-console-*` Release、组件 manifest 和 SHA256 清单。
- [ ] TODO：在 xworkmate-bridge 仓库建立 build job，发布 `runtime-xworkmate-bridge-*` Release、组件 manifest 和 SHA256 清单。
- [ ] TODO：在 QMD 仓库建立 build job，发布 `runtime-qmd-*` Release、组件 manifest 和 SHA256 清单。
- [ ] TODO：为 amd64、arm64 分别产出可安装资产；若资产与发行版相关，则按支持矩阵拆分并在 manifest 中明确兼容范围。
- [ ] TODO：增加 Release 契约测试，拒绝缺失入口、manifest、SHA256 或架构资产的发布。

### P0：离线聚合与目标机免构建

- [ ] TODO：实现 offline package job，按固定 tag 下载四组件 Release，并在聚合前逐文件执行 SHA256 校验。
- [ ] TODO：生成可追溯聚合 manifest，并为最终 `offline-package-*` 生成和发布 SHA256。
- [ ] TODO：在目标机部署入口强制 prebuilt-only，删除或禁用四组件所有源码构建回退。
- [ ] TODO：增加“目标机禁止构建”守卫，检测到编译器调用、包构建命令、源码 checkout 或镜像构建即失败。
- [ ] TODO：在断网或仅允许访问 offline package 源的目标机上完成端到端部署验证。

### P1：并发、性能与可观测性

- [ ] TODO：实现在线 CPU 探测和 `<= 2 * 在线 CPU` 的全局并发硬限制。
- [ ] TODO：定义 load 采样窗口、收缩/恢复阈值、迟滞策略和最低并发，完成动态收缩测试。
- [ ] TODO：记录阶段级耗时、组件级耗时、并发变化和环境标签，产出结构化 JSON 及人类可读摘要。
- [ ] TODO：汇总部署耗时分布，至少输出 count/min/max/avg/P50/P90/P95/P99，并区分首次执行与幂等重跑。

### P1：幂等与平台矩阵

- [ ] TODO：为每个支持矩阵项连续执行至少两次，验证第二次无非预期 changed、服务中断、重复资源或凭据变化。
- [ ] TODO：覆盖 Debian 11/12/13 amd64/arm64。
- [ ] TODO：覆盖 Ubuntu 22.04/24.04/26.04 amd64/arm64。
- [ ] TODO：保存每个矩阵项的 Release tag、offline package SHA256、部署日志、耗时数据和验收结论。
- [ ] TODO：全部矩阵通过后，再把“计划支持”更新为“已验证支持”；部分通过时逐项记录，不做整体完成声明。
