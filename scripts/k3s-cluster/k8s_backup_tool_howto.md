# 📦 k8s_backup_tool 使用文档

> **版本：v1.15.22 | 脚本语言：Bash | 适配平台：Linux/macOS | 作者：你自己**
> 项目开发总耗时约 **12 小时+**，共计迭代 **22 个版本**，涵盖调试、S3 上传验证、权限保持恢复、节点备份解耦等关键优化。

---

## 📘 文档功能概要（Docs）

`k8s_backup_tool` 是一个用于 **Kubernetes 集群资源和节点数据的备份、恢复、删除和查看** 的自动化脚本工具。主要支持：

- ✅ 基于 Velero 的命名空间级别资源备份
- ✅ 节点数据目录打包上传 S3（支持多节点）
- ✅ 支持预处理（precmds）和后处理（postcmds）
- ✅ 使用 `tar` + `rsync` 实现完整权限/ACL/owner 的数据还原
- ✅ 支持 debug 模式，适合本地验证与 CI/CD 集成

---

## 🔧 使用前提 & 安装配置

### 系统依赖

```bash
velero aws jq yq rsync tar
```

请确保以上命令均可用，并已正确配置 AWS S3 访问凭证。

### YAML 配置文件示例 `k8s_backup_config.yaml`

```yaml
settings:
  VELERO_NAMESPACE: "velero"
  VELERO_BUCKET: "k8s-resources-backup"
  VELERO_REGION: "ap-northeast-1"
  AWS_ACCESS_KEY_ID: "xxx"
  AWS_SECRET_ACCESS_KEY: "xxx"

backup_config:
  cluster_name: deepflow-demo
  namespaces:
    - default
    - deepflow
  nodes:
    deepflow-demo: /var/lib/mysql/
  precmds: |
    echo "🔻 停止 MySQL..."
    kubectl scale deployment mysql -n deepflow --replicas=0
  postcmds: |
    echo "🚀 启动 MySQL..."
    kubectl scale deployment mysql -n deepflow --replicas=1
```

---

## 🚀 用法说明

### 查看备份列表

```bash
bash k8s_backup_tool.sh list -c k8s_backup_config.yaml
```

### 创建完整备份（资源 + 节点）

```bash
bash k8s_backup_tool.sh backup -c k8s_backup_config.yaml
```

### 恢复指定时间点的备份

```bash
bash k8s_backup_tool.sh restore -c k8s_backup_config.yaml <date_tag>
```

### 删除指定 date_tag 的备份

```bash
bash k8s_backup_tool.sh delete -c k8s_backup_config.yaml <date_tag>
```

### 启用调试模式（查看执行详情）

```bash
bash k8s_backup_tool.sh backup -c k8s_backup_config.yaml --debug
```

---

## 📅 主要版本变更日志（Change Log）

| 版本号      | 日期           | 主要改动                                                  |
|-------------|----------------|-----------------------------------------------------------|
| v1.0.0     | 初版           | 支持 Velero 备份/恢复                                      |
| v1.0.2     | +1 小时        | 支持 precmds / postcmds                                   |
| v1.0.8     | +1 小时        | delete 支持 selector，调试查询输出                        |
| v1.0.12    | +2 小时        | 修复 Velero date_tag 匹配问题，增加 label fallback         |
| v1.0.16    | +2 小时        | 支持 S3 节点数据备份、--debug 模式                        |
| v1.0.21    | +3 小时        | 解压使用 tar + rsync 保留所有权限和 ACL                   |
| **v1.0.22    | ✅ 当前版本    | 🎉 解耦备份逻辑、完整恢复链路、安全检查、节点并行支持等 |

> ⏱ 累计开发与测试耗时约 **12 小时+**，包含脚本编写、调试、数据验证、权限恢复验证等

---

## 🧭 项目演进计划

| 实现方式        | 语言/平台 | 状态    | 说明                                |
|----------------|-----------|---------|-------------------------------------|
| Bash 脚本版     | Bash      | ✅ 已完成 | 当前主力版本，稳定可用              |
| Go CLI 工具     | Go        | 🧪 计划中 | 计划提供跨平台二进制，支持多线程    |
| GitHub Actions | JavaScript| 🧪 计划中 | 适配自动备份工作流与企业 CI 场景    |

---

## ❤️ 鸣谢

感谢你一路坚持调试与迭代。这个项目不仅提升了自动化能力，也沉淀了跨平台备份与恢复的最佳实践。如果你希望贡献或提问，欢迎 PR 或 Issues！
