# LiteLLM AI API Gateway 部署与运维指南

本文档整理了在一台 Ubuntu 单机 VPS 上部署、维护以及排错 Caddy + LiteLLM + PostgreSQL 的操作指南。
有关网关的架构设计和外部 API 接入规范，请参阅 [Readme.md](./Readme.md)。

## 1. 部署步骤

**第一步：检查系统环境**
登录目标 VPS：
```bash
ssh ubuntu@jp-xhttp-contabo.svc.plus
```

**第二步：初始化数据库**
为了安全起见，不要把 LiteLLM 表结构混入业务库，不要使用 `postgres` 超级账号直接连接 LiteLLM。请在 Postgres 中执行：
```sql
CREATE USER litellm WITH PASSWORD 'replace-with-strong-password'; 
CREATE DATABASE litellm OWNER litellm; 
GRANT ALL PRIVILEGES ON DATABASE litellm TO litellm; 
\c litellm 
GRANT ALL ON SCHEMA public TO litellm; 
ALTER SCHEMA public OWNER TO litellm;
```

**第三步：测试数据库连接**
```bash
# 原始直连 (供调试)
psql "postgresql://litellm:replace-with-strong-password@127.0.0.1:5432/litellm?sslmode=disable" -c "SELECT 1;"

# 通过 TLS Proxy (生产使用，由 stunnel 提供)
psql "postgresql://litellm:replace-with-strong-password@127.0.0.1:15432/litellm?sslmode=require" -c "SELECT 1;"
```

**第四步：部署配置**
在 Ansible 控制机运行 Playbook 自动化部署（此步骤会自动创建配置模板、环境变量并启动服务）：
```bash
ansible-playbook -i inventory.ini setup-litellm.yaml --limit jp-xhttp-contabo.svc.plus --vault-password-file ~/.vault_password
```

**第五步：检查服务运行端口**
验证所需的端口是否在监听状态：
```bash
sudo ss -lntp | egrep '4000|5432|15432'
```

---

## 2. 日志与排错命令

如果 LiteLLM 网关或 Caddy 出现请求拦截、连通性或鉴权等问题，可以通过以下命令快速排查日志：

```bash
# 1. 查看 LiteLLM 代理服务核心日志 (最常用)
journalctl -u litellm-proxy -n 200 -f

# 2. 查看 PostgreSQL TLS 隧道日志
journalctl -u stunnel-postgres-client -n 200 -f

# 3. 查看 Caddy 系统层日志 (排查证书或启动问题)
journalctl -u caddy -n 200 -f

# 4. 查看 Caddy 访问日志 (API Gateway 流量)
tail -f /var/log/caddy/api.svc.plus.access.log 

# 5. 查看 Caddy 访问日志 (Admin UI 流量)
tail -f /var/log/caddy/litellm.svc.plus.access.log
```

---

## 3. 备份与升级说明

> [!CAUTION]
> 升级 LiteLLM 时经常会触发底层 Prisma 数据库迁移 (DB Migration)。**请始终在升级前完整备份数据库。**
> 绝对不要手工干预或使用第三方工具修改 LiteLLM 的原生表结构。

**备份命令:**
```bash
# 创建备份存放目录
mkdir -p /var/backups/litellm

# 导出完整的 PostgreSQL 备份文件
pg_dump -Fc "postgresql://litellm:replace-with-strong-password@127.0.0.1:5432/litellm?sslmode=disable" \
        -f /var/backups/litellm/litellm-$(date +%F).dump
```

**恢复命令 (如需):**
```bash
pg_restore -d "postgresql://litellm:replace-with-strong-password@127.0.0.1:5432/litellm?sslmode=disable" \
           -1 /var/backups/litellm/litellm-xxxx-xx-xx.dump
```

---

## 4. 安全边界说明

- **公网暴露**：只开放 `443` 端口。内部服务组件（LiteLLM 监听 `4000` 端口, PostgreSQL 监听 `5432` 端口, Stunnel 监听 `15432` 端口）**全部且仅**绑定到 `127.0.0.1` 本地回环，完全阻断公网直连扫描。
- **环境隔离**：生产环境中存储了主密钥、加盐以及 DB 密码的 `/etc/litellm/litellm.env` 文件，权限必须被严格限制为 `600`。
- **证书管理**：Caddy 自动处理 HTTPS 并在前端封堵非白名单 API 路径。
- **凭证轮换机制**：若 `LITELLM_MASTER_KEY` 或 `LITELLM_SALT_KEY` 曾被输出到终端或截图发送在任何聊天记录中，请立即轮换，**绝对不要推入代码仓库**！
