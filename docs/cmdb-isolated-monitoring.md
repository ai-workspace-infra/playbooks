# 基础监控独立接入

`deploy_cmdb_monitoring.yml` 将 2026-10-17 实际接入配置固化为
`vhosts/cmdb-monitoring` Role，适用于现有业务节点的 CPU、内存、磁盘、网络采集。
它不安装软件包、不修改业务服务、现有 Vector 配置、日志采集、数据库或网络规则。
逐台执行，只有配置或二进制发生变化才重启独立探针。

## 已落地配置

| 项目 | 配置 |
| --- | --- |
| Node Exporter | 1.8.2，127.0.0.1:19100，30 秒采集 |
| Vector | 0.41.1，单线程，仅 remote write |
| 服务名 | `cmdb-node-exporter`、`cmdb-metrics-agent` |
| 二进制 | `/opt/cmdb-monitoring/` |
| 凭据 | `/etc/cmdb-monitoring/vector.toml`，root 0600；systemd LoadCredential |
| 权限 | DynamicUser、NoNewPrivileges、ProtectSystem、ProtectHome |
| CPU 上限 | 每个独立探针 10% 单核 |
| 内存上限 | exporter 96 MiB、Vector 160 MiB |
| 网络 | exporter 仅回环，HTTPS 出站到 Observability |
| 版本校验 | 固定版本、架构与 archive SHA-256；当前仅验证 amd64 |

Shared 是单一逻辑环境，标签 `environment=shared`、
`logical_project=open-platform-shared`；`project_id` 记录真实云项目 ID。
两个字段用途不同，原生项目 ID 不自动改写成逻辑名称。
主机、分组、SSH 用户和可信 host key 均来自 IaC 生成的 CMDB inventory；
本仓库不再维护一份人工主机清单。调用方从 GitOps 声明传入以下非秘密参数：

```yaml
cmdb_monitoring_environment: shared
cmdb_monitoring_provider: gcp
cmdb_monitoring_logical_project: open-platform-shared
cmdb_monitoring_project_id: <verified-native-project-id>
cmdb_monitoring_instance: <verified-resource-name>
```

## 部署和验证

控制端通过既有 Vault/OIDC 流程注入 `VECTOR_AUTH_USER`、`VECTOR_AUTH_PASSWORD`。
现有凭据源为 `kv/data/CICD/observability` 的 `user`、`password` 字段；
新的路径规划必须由调用方显式适配。本 Role 不读取其他主机的配置来复制密码，
不创建 Vault 记录或 bootstrap 脚本，不提供默认密码。

```sh
ansible-playbook -i <iac-cmdb-inventory> deploy_cmdb_monitoring.yml \
  -e @<non-secret-monitoring-vars.yml> --limit <verified-host> --check
ansible-playbook -i <iac-cmdb-inventory> deploy_cmdb_monitoring.yml \
  -e @<non-secret-monitoring-vars.yml> --limit <verified-host>
```

`--check` 检查声明、目标与 unit 模板；首次安装的二进制下载和 Vector 验证只在真实执行时完成。
凭据任务关闭 diff 和日志；仅新探针服务可由该入口重启。
服务运行集合检查会阻止在已有服务停止时继续下一台，但不能代替应用级验收。
最后从 VictoriaMetrics 验证新样本，而非仅以 systemd active 判断接入成功：

```promql
time() - timestamp(node_uname_info)
count by(instance)(count by(instance,__name__)({__name__=~"node_cpu_seconds_total|node_memory_MemTotal_bytes|node_filesystem_size_bytes|node_network_receive_bytes_total"}))
```

第二条查询每个主机应返回 4。线上已验证 7 个实例：JP、SG、US、PH、Vault、
Observability、PROD Web；此回执来自 2026-10-17 部署，不代表所有云资源已覆盖。
Lightsail 和 3 条 Shared 旧实例记录的访问及迁移关系仍待确认。

## 既有 Vector 节点

Vault 和 PH 继续使用现有 `vector.service`、`node-exporter.service`。
本次修复仅重启 Vector；Vault 增补 Shared/GCP/原生项目/逻辑项目标签。
既有 `vector-agent` Role 模板已补 `LimitNOFILE=524288` 和可选项目标签，供后续正规部署保留配置。
调用方分别传入 `vector_observability_environment`、`vector_observability_provider`、
`vector_observability_project_id`、`vector_observability_logical_project`。
不将这两台加入新独立探针组，以免重复采集。

## 回滚

```sh
ansible-playbook -i <iac-cmdb-inventory> deploy_cmdb_monitoring.yml \
  --limit <verified-host> -e cmdb_monitoring_state=stopped
```

只停用两个独立服务，保留配置、二进制和缓冲目录；恢复时使用默认 `present`。
既有 Vector 节点不使用此回滚入口。业务服务健康、查询样本新鲜度和采集器资源占用需分别核验。
