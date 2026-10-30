# Linux OS 三类操作身份：最小实施草案

状态：OS 账户设计已收敛；尚未实现账户任务、写入 Vault 或在主机执行。

## 1. 职责边界

`vhosts/common` 仅负责 Linux OS 基线：主机名与身份检查、用户/组、SSH 公钥授权、受控 sudo、目录与文件权限，以及 OS 侧允许/拒绝验收。

所有 Linux 节点采用同一套用户名，不因是否安装 Kubernetes 而改变。

- K3s/K8s 的认证主体、kubeconfig、RBAC 和集群权限验收由对应集群 role 实现。
- 数据迁移及数据库连接授权由对应迁移/数据库 role 实现，调用方显式选择执行角色。
- Docker 安装和运行配置由对应运行时 role 实现；common 仅负责 OS 账户和权限边界。

上述专业 role 消费 common 已建立的 OS 用户，不在 common 中创建集群凭据、数据库角色或迁移连接。

## 2. 统一用户名与 SSH 登录

| Linux 用户 | 固定角色 | OS 职责与权限 |
| --- | --- | --- |
| `root` | 系统管理 | 保留现有 root；用于初始化、安装、恢复和必要管理，平时不用作业务进程身份 |
| `operator` | 日常运行操作 | 通用操作账户，可 SSH 公钥登录；操作被授权的服务和工作目录，通过受控 sudo 执行必要运行操作 |
| `auditor` | 只读审计与分析 | 可 SSH 公钥登录；读取授权系统状态及脱敏输出；无通用 sudo、系统配置写权限、运行时 socket 或管理凭据访问权限 |

三者通过受信管理网络使用独立公钥/证书授权，禁用密码认证；root 使用 `PermitRootLogin prohibit-password`。保留现有管理入口直至新身份登录及权限验收通过；先验证 sshd 配置，再 reload。密码锁定与公钥登录须在实际 OS/PAM 配置下验证。

`auditor` 的只读针对系统配置和业务状态，允许在自己的受控工作目录保存报告或授权导出文件。不能仅凭用户名、默认无 sudo 或脚本自律认定只读；需检查有效组、文件权限、ACL、sudoers 和可执行入口。

`operator` 为通用保留名：接管前检查现有用户、UID/GID、文件归属和组；发现同名用户或未知权限时停止，不直接接管、改名或重置。

日常运行操作员是人或自动化操作身份；实际应用进程另用非 root 服务身份，不继承操作员的 sudo 或管理权限。rootful Docker socket、`docker` 组和任意 sudo 都具备接近 root 的主机管理能力；不得在授予这些权限后声称 operator 与 root 强制隔离。确需这些能力时按显式系统管理阶段处理，审计账户不得获得。

## 3. OS 所需 Vault KV 路径

使用 KV v2；`<environment>`、`<node>` 必须由声明显式提供，缺失则停止，不回退到生产路径。以下为 CLI 逻辑路径，API/policy 路径在 `kv/` 后加入 `data/`；metadata 权限单独评审。

| 用途 | KV CLI 路径 | 规划字段与消费者 |
| --- | --- | --- |
| 主机 OS 身份绑定 | `kv/<environment>/platform/nodes/<node>/identity-bindings` | 三用户名、SSH 授权引用及受控 OS 权限配置；common 读取，GitOps 只保存非敏感引用 |
| root SSH 授权 | `kv/<environment>/platform/nodes/<node>/ssh/root` | `authorized_keys`；受控 common 管理身份读取并部署公钥 |
| operator SSH 授权 | `kv/<environment>/platform/nodes/<node>/ssh/operator` | `authorized_keys`；受控 common 管理身份读取 |
| auditor SSH 授权 | `kv/<environment>/platform/nodes/<node>/ssh/auditor` | `authorized_keys`；受控 common 管理身份读取 |

此表只保留 OS 记录，不包含 Kubernetes 身份映射、迁移源连接、目标写入或管理连接路径。

SSH 优先由操作员各自持有私钥；Vault 保存公钥授权或提供短期 SSH 证书，不默认创建三个共享私钥。自动化私钥确有需要时使用调用方已有的独立受控凭据路径，不放入共享授权记录。common 消费已授权的 Vault 读取能力，不自行创建或修改 Vault policy。

V6 示例参数为 `environment=prod`、`node=prod-k3s-v6`，例如 `kv/prod/platform/nodes/prod-k3s-v6/ssh/operator`。这些仅为路径规划，尚未写入 Vault。既有 `kv/prod/platform/prod-k3s-v6/ssh` 在消费者切换验收前保持可用，不删除、不复制秘密到新路径。

## 4. 新 OS 节点接管顺序

以后每次接管新 Linux 节点，标准入口先执行 `vhosts/common`：

1. 核实受信 host key、CMDB/inventory、OS、主机名和既有用户/权限，记录脱敏基线。
2. 使用 root 建立或核实 operator/auditor，配置各自公钥、受控 sudo、工作目录和 SSH 策略。
3. 检查父目录、ACL、sudoers、有效组和敏感文件访问；先验证配置，再 reload SSH。
4. 分别验证三用户实际 SSH 登录、OS 允许/拒绝和必要运行操作，保留回执及管理恢复入口。
5. OS 基线通过后，由调用方进入服务、运行时、K3s/K8s 或迁移等对应 role。

新接管入口必须显式启用此基线；通用 role 保留参数开关，避免已有 common 调用方升级后自动批量改账户。接管旧节点先盘点，再逐节点变更。

## 5. 专业 role 的执行身份选择

专业 role 执行时显式选择或通过参数指定以下角色，不根据任务名称自动提权：

| 角色选择 | OS 用户 | 调用原则 |
| --- | --- | --- |
| 系统管理 | `root` | 初始化、安装、恢复及确需管理能力的步骤；记录调用者、授权范围和结果 |
| 日常运行操作 | `operator` | 授权范围内的运行操作；目标迁移写入复用 operator，不新增迁移 Linux 用户 |
| 只读审计 | `auditor` | 只读检查、分析及授权源端导出/比对；不能用于系统或业务状态写入 |

迁移调用方分别显式指定源、目标和每个步骤的执行角色；身份缺失或权限不满足时停止，不静默回退到 root。需要更高权限时显式切换系统管理阶段，完成后返回普通操作权限。OS 用户选择不自动赋予数据库或 Kubernetes 权限，由对应 role 校验授权。

本节仅约定跨 role 的身份选择边界，common 不执行迁移、不读取迁移连接，也不实现集群授权。

## 6. 现有 cluster-audit 的 OS 侧渐进替换

新节点统一使用 `auditor`。旧节点先创建并验证 auditor，再逐个迁移 Linux 审计调用方和 OS 验证脚本；确认 OS 消费者已切换，且其他 role 已确认没有依赖后，才单独评审停用旧 cluster-audit。

不直接重命名旧账户、不递归修改数据卷所有权、不批量撤权。其他 role 对旧身份的引用由各自 owner 处理。历史回执保留原用户名，不能改写为 auditor 已完成验收。

## 7. 最小实现与 OS 验收

| 检查 | 通过条件 |
| --- | --- |
| 显式启用 | 默认不影响已有调用方；新接管入口显式启用；目标身份和变量缺失时停止 |
| root | 现有管理员仍可 SSH 登录和执行必要管理，避免锁死；日常业务进程不使用其身份 |
| operator | 可 SSH 登录、访问授权工作目录及执行规定运行命令；越权 sudo 和管理秘密读取被拒绝 |
| auditor | 可 SSH 登录并读取授权 OS 状态；实际拒绝系统/业务状态写入、管理秘密读取和提权；允许个人受控目录写入 |
| SSH 与 sudo | 语法校验通过、用户独立授权、父目录/ACL/组及实际访问符合预期；配置变更才 reload |
| 幂等与恢复 | 重跑不重建用户或无故重启；保留老管理入口；新增访问可独立撤销，不删除未知配置 |

现有 `vhosts/common/tasks/node_bootstrap.yml` 仅建立锁定登录的 cluster-audit 和有限 OS 检查，尚未实现本文的三用户 SSH 和完整 OS 权限交付。后续实现范围仅为上述 OS 基线，不捆绑集群身份或数据迁移。
