# OrbStack 10 大核心服务单机部署与验证套件

本目录包含《图解平台工程 · 云中立实践》10 大核心服务类别在 macOS 本机 OrbStack 环境下的单机 Docker Compose 部署配置与自动化健康探测套件。

## 目录结构

- `01-git-ci-gitea/`: Gitea 代码托管与轻量级 Actions 运行引擎 (`:13000`, `:13022`)
- `02-artifact-harbor/`: Harbor 云原生制品管理仓库 (`:18080`, 生产拓扑为 `:8080`/`:443`)
- `03-s3-storage-silo/`: pgsty/silo S3 兼容对象存储 (`:19000`, `:19001`)
- `04-cmdb-inventory/`: Ansible 动态资产清单与 CMDB SQLite API (`:18000`)
- `05-identity-vault-zitadel/`: HashiCorp Vault 密钥中枢与 Zitadel 身份后端 (`:18200`, `:15434`)
- `06-gitops-doco-cd/`: Doco CD 单机声明式 GitOps 持续交付 (`:19088`, `:19128`)
- `07-container-vector-node/`: PostgreSQL 16 + pgvector 向量底座 (`:15435`)
- `08-pg-backup-drill/`: PostgreSQL 灾备演练源数据库 (`:15436`)
- `09-ai-gateway/`: New API 认证管理 + LiteLLM 路由分发网关 (`:13001`, `:14000`)
- `10-mcp-server/`: Infrastructure MCP Server 守护网关 (`:18888`)
- `verify-orbstack-all-services.sh`: 一键拉起 10 个 Compose 工程并执行自动化健康收敛探测的验证脚本。

## 运行方式

```bash
chmod +x ./verify-orbstack-all-services.sh
./verify-orbstack-all-services.sh
```

## 规范文档

详细架构拓扑、端口分配矩阵与真实探测证据，请参阅：
`docs/reference/platform-core-services-orbstack-acceptance-matrix.zh.md`
