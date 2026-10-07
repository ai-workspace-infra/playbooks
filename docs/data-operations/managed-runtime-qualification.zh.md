# Accounts/Billing 受控运行镜像资格

Owner 为 Playbooks `scripts/data_operations/selfhost/managed_runtime_host.py`。
本阶段只运行短暂待机探针，结束后删除自己创建的容器，不替代应用部署或切换。

## 输入与执行边界

- 非秘密 manifest 同时固定 Accounts 和 Billing 的完整源码 SHA、匹配的 GHCR `sha-<commit>` tag 和 manifest digest；拒绝其他环境、主机、服务、字段及调用方提供的数据库地址或 primary 角色。
- 实际镜像资格执行需 `MANAGED_RUNTIME_GATE_VERIFIED=true`；默认 preview 只拉取固定镜像、不启动服务。此断言由 Toolkit 审核，后续调用方还须绑定成功资源回执、原始 CMDB、固定 GitOps、同轮 IaC 临时访问与本轮生产环境审核。owner action `prod-managed-runtime`、独立 Ansible role 与 canonical CMDB/固定 GitOps/同轮访问守卫已提供，生产调用方尚未接入。这个只读业务边界的短暂镜像检查不读取/写入 DB；初始化/复制的独立数据审核仍单独保留，不能使用镜像资格回执代替。
- 只接受 stdin 中的 Registry username/token，不接受数据库密码、来源 DSN 或任何 UAT 凭据。复用经过资格验证的私密 tmpfs registry session 并清理。
- 强制 `standby`、后台关闭、network none、只读根文件系统、所有 capability 移除、no-new-privileges、无端口发布、无挂载、restart=no、CPU/内存/PID 限制。
- 数据库地址仅为无密码的 `127.0.0.1:1/account` 配置指纹。端口不可用且没有外网，待机服务不得连接 DB；该指纹不能证明实际数据库身份。
- Accounts 直接调用预编译 `/usr/local/bin/account --config /dev/null`，覆盖旧 ENTRYPOINT，避免生成配置、读取镜像内的旧环境模板或启动 stunnel。Billing 直接调用预编译 `/app/billing-service`。
- 实际检查容器 image、entrypoint、env、安全/网络/挂载/端口约束，再核对 IMAGE 派生的 release、角色、后台/启动写者、schema_version=0。healthz/ping 返回 200；readyz 和 GET/POST/PUT/PATCH/DELETE 业务请求均须返回 503。任意失败均删除本次 UUID 容器，清理失败不能生成成功回执。

回执包含镜像合同和待机资格状态；`database_connected=false`、`schema_verified=false`、`application_deployed=false`、`database_cutover_approved=false`。不读取源库、不写业务数据、不更新 Caddy、DNS、云资源、Registry 资源或 CMDB，不启动其他服务/后台任务。

## 隔离资格与真实接受

`managed-runtime-container` CI 固定 Accounts `fabe68a85b4d90826dccdfe6a2116ef025f475d4` 与 Billing `f11e875f74cbe0154e49f6310b6697eff65f731e`，构建完整服务 package，仅封装为一次性 Ubuntu/Alpine binary fixtures。只有 fixture 镜像引用替换为本地名称，其余参数、探针及容器约束使用 owner；检查函数的 fixture image override 不对 CLI 开放，实际 execute 始终拉取和验证 manifest digest。Fixtures 不含业务数据、环境模板、数据库凭据，不发布为部署 artifact。

两份正式 GHCR 镜像不允许匿名拉取。此 CI 不取得生产 Vault 或 Registry 凭据，不能声称正式发布镜像已拉取、生产主机已部署或原生数据库已验收。正式 pull/角色资格需后续受控生产调用方执行并保存原始回执。

之后仍须按顺序完成实际原生初始化、Billing 第 53 表增量、完整只读复制、全写者冻结/最终追平、10 分钟内完整一致性、单写者、主角色与正常业务配置部署、Accounts/Billing 联动入口切换和生产验收。待机使用的空配置不能作为 primary 业务配置；primary 的认证、内服务、运行凭据与私网 ingress 需另行绑定真实合同。

Billing #46 修复了待机 config.Load 仍要求内部业务 Token 的启动缺口；主角色和原业务角色仍要求该 Token。容器资格使用这份已合并的修复源码，不注入任何占位业务 Token。
