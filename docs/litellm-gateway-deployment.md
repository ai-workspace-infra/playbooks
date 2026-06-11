# LiteLLM Gateway 部署指南

## 目标架构

```
                    ┌─────────────────────────────────────────┐
                    │           Caddy (HTTPS Entry)             │
                    │                                         │
│  ┌──────────────────────────────────┐   │
                    Internet ──────────►│  │  api.svc.plus/v1/openai/*       │   │
                    │  │  api.svc.plus/v1/anthropic/*     │   │
                    │  │  api.svc.plus/ui/*              │   │
                    │  └──────────────────────────────────┘   │
                    └──────────────────┬──────────────────────┘
                                       │
                    ┌──────────────────▼──────────────────────┐
                    │          LiteLLM Proxy (127.0.0.1:4000)  │
                    │                                          │
                    │  ┌──────────────────────────────────┐   │
                    │  │  /v1/chat/completions (OpenAI)   │   │
                    │  │  /v1/messages (Anthropic)        │   │
                    │  │  /ui (Admin Dashboard)           │   │
                    │  └──────────────────────────────────┘   │
                    └──────────────────┬──────────────────────┘
                                       │
                    ┌──────────────────▼──────────────────────┐
                    │        Model Providers (External)        │
                    │                                          │
                    │  • OpenAI (GPT-4o-mini)                  │
                    │  • Anthropic (Claude 3.5 Sonnet)         │
                    │  • DeepSeek (deepseek-chat)              │
                    │  • Local Models (OAI-compatible)         │
                    └─────────────────────────────────────────┘
```

## 推荐目录结构

```
/etc/litellm/
├── config.yaml           # LiteLLM 配置文件
└── litellm.env           # 环境变量 (包含 API Keys)

/etc/systemd/system/
└── litellm-proxy.service # systemd 服务单元

/etc/caddy/conf.d/
└── litellm.caddy         # Caddy 路由配置
```

## 一、Caddyfile 配置示例

```caddy
# /etc/caddy/conf.d/litellm.caddy

# API Gateway + LiteLLM Admin UI (统一入口)
api.svc.plus {
    # LiteLLM Admin UI (Basic Auth 保护)
    @ui_admin {
        path /ui/*
    }

    @ui_admin_unauthorized {
        not header Authorization "Basic *"
    }

    handle @ui_admin_unauthorized {
        respond "Unauthorized" 401 {
            www-authenticate Basic realm="LiteLLM Admin UI"
        }
    }

    handle @ui_admin {
        reverse_proxy 127.0.0.1:4000
    }

    # OpenAI-Compatible API
    @openai_api {
        path /v1/openai/*
    }

    handle @openai_api {
        rewrite * /v1{path}
        reverse_proxy 127.0.0.1:4000 {
            flush_interval -1
            transport http {
                dial_timeout 30s
                read_timeout 600s
                write_timeout 600s
            }
        }
    }

    # Anthropic-Compatible API
    @anthropic_api {
        path /v1/anthropic/*
    }

    handle @anthropic_api {
        rewrite * /v1{path}
        reverse_proxy 127.0.0.1:4000 {
            flush_interval -1
            transport http {
                dial_timeout 30s
                read_timeout 600s
                write_timeout 600s
            }
        }
    }

    # 通用代理
    handle {
        reverse_proxy 127.0.0.1:4000
    }

    encode gzip zstd

    header {
        X-Real-IP
        X-Forwarded-For
        X-Forwarded-Proto
        Host
    }

    log {
        output file /var/log/caddy/litellm.access.log
    }
}
```

### 关键路径映射

| 外部路径                                | 内部路径                    | 说明           |
|---------------------------------------|--------------------------|--------------|
| `https://api.svc.plus/v1/openai/chat/completions` | `http://127.0.0.1:4000/v1/chat/completions` | OpenAI 兼容 API |
| `https://api.svc.plus/v1/anthropic/messages` | `http://127.0.0.1:4000/v1/messages` | Anthropic 兼容 API |
| `https://api.svc.plus/ui/*`           | `http://127.0.0.1:4000/ui/*` | Admin UI (Basic Auth) |
| `https://api.svc.plus/v1/chat/completions` | `http://127.0.0.1:4000/v1/chat/completions` | 短路径兼容 (可选) |

---

## 二、LiteLLM config.yaml 示例

```yaml
# /etc/litellm/config.yaml

model_list:
  # OpenAI 模型
  - model_name: gpt-4o-mini
    litellm_params:
      model: openai/gpt-4o-mini
      api_key: os.environ/OPENAI_API_KEY

  # Anthropic 模型
  - model_name: claude-sonnet
    litellm_params:
      model: anthropic/claude-3-5-sonnet-latest
      api_key: os.environ/ANTHROPIC_API_KEY

  # DeepSeek 模型
  - model_name: deepseek-chat
    litellm_params:
      model: deepseek/deepseek-chat
      api_key: os.environ/DEEPSEEK_API_KEY

  # 本地 OpenAI-Compatible 模型
  - model_name: local-qwen
    litellm_params:
      model: openai/qwen
      api_base: http://127.0.0.1:8000/v1
      api_key: os.environ/LOCAL_MODEL_API_KEY

general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY
  drop_rate_limit_requests: true
  set_verbose: false

router_settings:
  model_group_alias:
    gpt-4o-mini: gpt-4o-mini
    claude-sonnet: claude-sonnet
    deepseek-chat: deepseek-chat
  routing_strategy: latency-based-routing
  enable_pre_call_checks: false
  retry_after: 60
  num_retries: 3

litellm_settings:
  drop_params: true
  set_verbose: true
  request_timeout: 600
  telemetry: false
  max_parallel_requests: 1000

environment_variables:
  OPENAI_API_KEY: os.environ/OPENAI_API_KEY
  ANTHROPIC_API_KEY: os.environ/ANTHROPIC_API_KEY
  DEEPSEEK_API_KEY: os.environ/DEEPSEEK_API_KEY
  LOCAL_MODEL_API_KEY: os.environ/LOCAL_MODEL_API_KEY
  LITELLM_MASTER_KEY: os.environ/LITELLM_MASTER_KEY
```

---

## 三、litellm.env 示例

```bash
# /etc/litellm/litellm.env

# API Keys (从环境变量读取)
OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxx
ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxxxxxxxxxxxxxxxxxx
DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxx
LOCAL_MODEL_API_KEY=sk-local-placeholder

# LiteLLM Master Key (必须设置，用于 API 认证)
LITELLM_MASTER_KEY=your-secure-random-master-key-here-min-32-chars

# 可选配置
# LITELLM_SALT_KEY=your-salt-key
# DATABASE_URL=postgresql://user:pass@host:5432/litellm
```

**文件权限**: `chmod 600 /etc/litellm/litellm.env`

---

## 四、systemd 服务单元示例

```ini
# /etc/systemd/system/litellm-proxy.service

[Unit]
Description=LiteLLM Proxy Service
After=network.target

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu
EnvironmentFile=/etc/litellm/litellm.env
ExecStart=/usr/local/bin/litellm \
    --host 127.0.0.1 \
    --port 4000 \
    --config /etc/litellm/config.yaml
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=litellm-proxy

[Install]
WantedBy=multi-user.target
```

---

## 五、部署步骤

### 1. 安装依赖

```bash
# 安装 Python 和 pip
apt update && apt install -y python3 python3-pip python3-venv

# 使用 pipx 安装 LiteLLM (推荐)
pip install pipx
pipx install litellm

# 或直接用 pip 安装
pip install litellm
```

### 2. 创建配置目录

```bash
mkdir -p /etc/litellm
chmod 755 /etc/litellm
```

### 3. 写入配置文件

```bash
# 写入 config.yaml
cat > /etc/litellm/config.yaml << 'EOF'
model_list:
  - model_name: gpt-4o-mini
    litellm_params:
      model: openai/gpt-4o-mini
      api_key: os.environ/OPENAI_API_KEY
  # ... 其他模型
EOF

# 写入环境变量文件
cat > /etc/litellm/litellm.env << 'EOF'
OPENAI_API_KEY=sk-xxx
ANTHROPIC_API_KEY=sk-ant-xxx
DEEPSEEK_API_KEY=sk-xxx
LITELLM_MASTER_KEY=your-secure-master-key
EOF

chmod 600 /etc/litellm/litellm.env
chmod 640 /etc/litellm/config.yaml
```

### 4. 部署 systemd 服务

```bash
cat > /etc/systemd/system/litellm-proxy.service << 'EOF'
[Unit]
Description=LiteLLM Proxy Service
After=network.target

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu
EnvironmentFile=/etc/litellm/litellm.env
ExecStart=/usr/local/bin/litellm --host 127.0.0.1 --port 4000 --config /etc/litellm/config.yaml
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=litellm-proxy

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable litellm-proxy
systemctl start litellm-proxy
systemctl status litellm-proxy
```

### 5. 配置 Caddy

```bash
# 确保 Caddy 导入 conf.d 目录
echo 'import /etc/caddy/conf.d/*.caddy' >> /etc/caddy/Caddyfile

# 创建 litellm Caddy 配置
cat > /etc/caddy/conf.d/litellm.caddy << 'EOF'
# ... 见上面的 Caddyfile 配置
EOF

# 验证并重载
caddy validate --config /etc/caddy/Caddyfile
systemctl reload caddy
```

### 6. 验证部署

```bash
# 检查 LiteLLM 健康状态
curl http://127.0.0.1:4000/health

# 检查 API Gateway
curl -X POST "https://api.svc.plus/v1/openai/chat/completions" \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"deepseek-chat","messages":[{"role":"user","content":"Hello"}]}'

# 访问 Admin UI
# https://api.svc.plus/ui/
```

---

## 六、API 验证命令

### 1. 健康检查

```bash
# 本地健康检查
curl http://127.0.0.1:4000/health

# 外部健康检查
curl https://api.svc.plus/health
```

### 2. OpenAI-Compatible API 测试

```bash
curl -X POST "https://api.svc.plus/v1/openai/chat/completions" \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "deepseek-chat",
    "messages": [
      {
        "role": "user",
        "content": "Hello from OpenAI-compatible endpoint"
      }
    ]
  }'
```

### 3. Anthropic-Compatible API 测试

```bash
curl -X POST "https://api.svc.plus/v1/anthropic/messages" \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -H "anthropic-version: 2023-06-01" \
  -d '{
    "model": "claude-sonnet",
    "max_tokens": 256,
    "messages": [
      {
        "role": "user",
        "content": "Hello from Anthropic-compatible endpoint"
      }
    ]
  }'
```

### 4. Admin UI 访问

```bash
# 如果启用了 Basic Auth
# 访问 https://api.svc.plus/ui/
# 使用配置的 admin 用户名和密码登录
```

---

## 七、安全注意事项

### 1. 网络隔离

- **4000 端口只监听 127.0.0.1**，不暴露到公网
- VPS 防火墙**不要开放 4000 端口**
- 对外只开放 **443** (HTTPS)
- **Caddy 是唯一公网入口**

### 2. Admin UI 保护

LiteLLM Admin UI **不应裸奔**，建议启用以下至少一种保护：

| 保护方式        | 说明                           |
|--------------|------------------------------|
| Basic Auth   | Caddy 内置，配置用户名密码            |
| IP 白名单      | 只允许特定 IP 访问 api.svc.plus/ui |
| Cloudflare Access | Cloudflare Zero Trust 认证     |
| VPN / Tailscale | 通过私有网络访问                  |

### 3. API 认证

- 所有 API 调用必须使用 `Authorization: Bearer <LITELLM_MASTER_KEY>`
- `LITELLM_MASTER_KEY` 必须足够长且随机 (建议 32+ 字符)

### 4. 文件权限

```bash
chmod 600 /etc/litellm/litellm.env    # 保护 API Keys
chmod 640 /etc/litellm/config.yaml     # 配置文件
```

---

## 八、Ansible 部署命令

```bash
# 部署 LiteLLM Gateway
ansible-playbook -i inventory.ini setup-litellm.yaml

# 指定 API Keys 部署
LITELLM_MASTER_KEY=your-secure-key \
OPENAI_API_KEY=sk-xxx \
ANTHROPIC_API_KEY=sk-ant-xxx \
DEEPSEEK_API_KEY=sk-xxx \
ansible-playbook -i inventory.ini setup-litellm.yaml

# 只部署 Caddy 配置 (不重启 LiteLLM)
ansible-playbook -i inventory.ini setup-litellm.yaml --tags litellm --start-at-task="Create LiteLLM Caddy fragment"
```

---

## 九、故障排查

### LiteLLM 服务无法启动

```bash
# 查看日志
journalctl -u litellm-proxy -f

# 验证配置
litellm --config /etc/litellm/config.yaml --test
```

### Caddy 配置无效

```bash
# 验证 Caddy 配置
caddy validate --config /etc/caddy/Caddyfile

# 查看 Caddy 日志
tail -f /var/log/caddy/litellm-*.log
```

### API 调用失败

```bash
# 检查端口绑定
ss -tlnp | grep 4000

# 测试本地连通性
curl http://127.0.0.1:4000/health

# 检查 API Key
source /etc/litellm/litellm.env
echo $LITELLM_MASTER_KEY
```

---

## 十、后续扩展

### 启用 PostgreSQL 数据库 (用于用量统计、团队管理等)

```bash
# 1. 安装 PostgreSQL
apt install -y postgresql postgresql-contrib

# 2. 创建数据库和用户
su - postgres
psql -c "CREATE USER litellm WITH PASSWORD 'your-password';"
psql -c "CREATE DATABASE litellm OWNER litellm;"
exit

# 3. 更新环境变量
echo "DATABASE_URL=postgresql://litellm:your-password@localhost:5432/litellm" >> /etc/litellm/litellm.env

# 4. 重启服务
systemctl restart litellm-proxy
```

### 集成 Vault (可选)

```bash
# 设置 Vault 环境变量
echo "VAULT_URL=https://vault.svc.plus" >> /etc/litellm/litellm.env
echo "VAULT_API_KEY_PATH=secret/litellm/api-keys" >> /etc/litellm/litellm.env
systemctl restart litellm-proxy
```

---

## 十一、Agent 接入配置

各 Agent 接入时只需配置 Base URL：

| Agent 类型      | Base URL                           | 认证            |
|--------------|-----------------------------------|---------------|
| OpenAI SDK   | `https://api.svc.plus/v1/openai`   | `LITELLM_MASTER_KEY` |
| Anthropic SDK | `https://api.svc.plus/v1/anthropic` | `LITELLM_MASTER_KEY` |
| LiteLLM SDK  | `https://api.svc.plus`             | `LITELLM_MASTER_KEY` |

示例 (Python):

```python
from openai import OpenAI

client = OpenAI(
    api_key="your-litellm-master-key",
    base_url="https://api.svc.plus/v1/openai"
)

response = client.chat.completions.create(
    model="deepseek-chat",
    messages=[{"role": "user", "content": "Hello"}]
)
```