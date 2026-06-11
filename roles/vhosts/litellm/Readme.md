# LiteLLM AI API Gateway 部署与接入指南

本指南详细整理了在一台 Ubuntu 单机 VPS 上，部署 Caddy + LiteLLM AI API Gateway + PostgreSQL Runtime DB + TLS DB Proxy 的完整方案。Ansible Playbook 均已提交到您的仓库 main 分支。

TIP

部署架构完全按照要求进行：无须引入 Nginx/APISIX 等，原生 Caddy 做前置，后端通过 stunnel 提供基于 127.0.0.1:15432 的高安全性 TLS 连接数据库。

1. 推荐目录结构
text

/etc/litellm/
├── config.yaml          # LiteLLM 核心配置文件 (模型路由/密钥等)
└── litellm.env          # 环境变量文件 (API 密钥、数据库连接串，权限 600)
/etc/caddy/conf.d/
├── api.svc.plus.caddy      # API Gateway 路由配置
└── litellm.svc.plus.caddy  # Admin UI 路由配置 (含 Basic Auth)
/etc/systemd/system/
└── litellm-proxy.service   # Systemd 服务定义文件
2. Caddy 配置
由于篇幅过长，在 Playbook 中这部分拆分为两个文件：

/etc/caddy/conf.d/api.svc.plus.caddy

caddyfile

api.svc.plus {
    encode zstd gzip
    handle /v1/models {
        reverse_proxy 127.0.0.1:4000 { flush_interval -1 }
    }
    # OpenAI-Compatible Endpoints
    handle_path /v1/openai/chat/completions {
        rewrite * /v1/chat/completions
        reverse_proxy 127.0.0.1:4000 {
            flush_interval -1
            transport http { dial_timeout 30s read_timeout 600s write_timeout 600s }
        }
    }
    handle_path /v1/openai/embeddings {
        rewrite * /v1/embeddings
        reverse_proxy 127.0.0.1:4000 { flush_interval -1 }
    }
    handle_path /v1/openai/responses {
        rewrite * /v1/responses
        reverse_proxy 127.0.0.1:4000 { flush_interval -1 }
    }
    # Anthropic-Compatible Endpoint
    handle_path /v1/anthropic/* {
        reverse_proxy 127.0.0.1:4000 { flush_interval -1 }
    }
    # Catch-all
    handle { respond "Not Found" 404 }
    log { output file /var/log/caddy/api.svc.plus.access.log }
}
/etc/caddy/conf.d/litellm.svc.plus.caddy

caddyfile

litellm.svc.plus {
    encode zstd gzip
    handle /ui* {
        basicauth {
            # username=admin, password=LITELLM_MASTER_KEY (hashed)
            admin $2a$14$b2oxMvD0p5ByjdCA18Go5u1qTjPeDjDzzXIanGVXdYIO6fvKf2cY.
        }
        reverse_proxy 127.0.0.1:4000 { flush_interval -1 }
    }
    
    handle { respond "Not Found" 404 }
    log { output file /var/log/caddy/litellm.svc.plus.access.log }
}
3. LiteLLM 配置 (/etc/litellm/config.yaml)
yaml

model_list:
  # GPT Family
  - model_name: gpt-5.5
    litellm_params:
      model: openai/gpt-5.5
      api_key: os.environ/OPENAI_API_KEY
    model_info: { mode: chat, supports_function_calling: true, context_window: 200000, max_tokens: 32000 }
  
  # DeepSeek Family
  - model_name: deepseek-chat
    litellm_params:
      model: deepseek/deepseek-chat
      api_key: os.environ/DEEPSEEK_API_KEY
    model_info: { mode: chat, supports_function_calling: true, context_window: 64000, max_tokens: 8192 }
  # Anthropic Models
  - model_name: claude-sonnet
    litellm_params:
      model: anthropic/claude-sonnet
      api_key: os.environ/ANTHROPIC_API_KEY
    model_info: { mode: chat, supports_vision: true, context_window: 200000, max_tokens: 4096 }
  # Embedding Models
  - model_name: embedding-default
    litellm_params:
      model: openai/text-embedding-3-small
      api_key: os.environ/OPENAI_API_KEY
    model_info: { mode: embedding, dimensions: 1536 }
  # Local Models
  - model_name: local-qwen
    litellm_params:
      model: openai/local-qwen
      api_base: http://127.0.0.1:8001/v1
      api_key: os.environ/LOCAL_MODEL_API_KEY
    model_info: { mode: chat, supports_function_calling: true }
database_url: postgresql://litellm:replace-with-strong-password@127.0.0.1:15432/litellm?sslmode=require
router_settings:
  routing_strategy: latency-based-routing
  enable_pre_call_checks: false
  retry_after: 60
  num_retries: 3
  
litellm_settings:
  drop_params: true
  set_verbose: false
  request_timeout: 600
  telemetry: false
  max_parallel_requests: 1000
4. 环境变量模板 (/etc/litellm/litellm.env.example)
bash

OPENAI_API_KEY=
ANTHROPIC_API_KEY=
DEEPSEEK_API_KEY=
MINIMAX_API_KEY=
LOCAL_MODEL_API_KEY=sk-local-placeholder
LITELLM_MASTER_KEY=sk-YOUR_MASTER_KEY
LITELLM_SALT_KEY=sk-YOUR_SALT_KEY
LITELLM_UI_USERNAME=admin
# 通过 TLS DB Proxy 访问 PostgreSQL:
DATABASE_URL=postgresql://litellm:replace-with-strong-password@127.0.0.1:15432/litellm?sslmode=require
PYTHONPATH=/home/ubuntu/.local/lib/python3.12/site-packages
5. PostgreSQL DB 与 SQL 初始化
WARNING

不要把 LiteLLM 表结构混入业务库，不要使用 postgres 账号直接连接。

sql

CREATE USER litellm WITH PASSWORD 'replace-with-strong-password'; 
CREATE DATABASE litellm OWNER litellm; 
GRANT ALL PRIVILEGES ON DATABASE litellm TO litellm; 
\c litellm 
GRANT ALL ON SCHEMA public TO litellm; 
ALTER SCHEMA public OWNER TO litellm;
测试连接命令：

bash

# 原始直连 (供调试)
psql "postgresql://litellm:replace-with-strong-password@127.0.0.1:5432/litellm?sslmode=disable" -c "SELECT 1;"
# 通过 TLS Proxy (生产使用)
psql "postgresql://litellm:replace-with-strong-password@127.0.0.1:15432/litellm?sslmode=require" -c "SELECT 1;"
6. LiteLLM Systemd 服务 (litellm-proxy.service)
ini

[Unit]
Description=LiteLLM Proxy Service
After=network-online.target
[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu
EnvironmentFile=/etc/litellm/litellm.env
ExecStart=/home/ubuntu/.local/share/xworkspace/litellm-venv/bin/litellm --host 127.0.0.1 --port 4000 --config /etc/litellm/config.yaml
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=litellm-proxy
[Install]
WantedBy=multi-user.target
7. 部署步骤
检查系统环境：登录目标 VPS ssh ubuntu@xworkmate-bridge.svc.plus
连接 PostgreSQL：确认 127.0.0.1:5432 正在监听。
初始化数据库：执行上述的 CREATE DATABASE / USER 语句。
检查 TLS Proxy：验证 stunnel-postgres-client 正确启动并监听了 127.0.0.1:15432。
部署配置：在 Ansible 控制机运行: ansible-playbook -i inventory.ini setup-litellm.yaml --limit jp-xhttp-contabo.svc.plus
启动服务: 检查 Caddy 和 Litellm 服务。
8. 验证命令
检查本地端口: sudo ss -lntp | egrep '4000|5432|15432'
健康检查: curl http://127.0.0.1:4000/health
Caddy 路由:
bash

curl -X POST "https://api.svc.plus/v1/openai/chat/completions" \
     -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
     -H "Content-Type: application/json" \
     -d '{ "model": "deepseek-chat", "messages": [{"role": "user", "content": "Hello"}] }'
9. 日志与排错命令
bash

journalctl -u litellm-proxy -n 200 -f
journalctl -u stunnel-postgres-client -n 200 -f
journalctl -u caddy -n 200 -f
tail -f /var/log/caddy/api.svc.plus.access.log 
tail -f /var/log/caddy/litellm.svc.plus.access.log
10. 备份与升级说明
CAUTION

升级 LiteLLM 可能触发数据库迁移。请始终在升级前备份。不要手工干预或修改 LiteLLM 的原生表结构。

备份命令:

bash

mkdir -p /var/backups/litellm
pg_dump -Fc "postgresql://litellm:replace-with-strong-password@127.0.0.1:5432/litellm?sslmode=disable" \
        -f /var/backups/litellm/litellm-$(date +%F).dump
11. 安全边界说明
公网只开放 443，内部服务 (LiteLLM:4000, PostgreSQL:5432, Stunnel:15432) 均只绑定 127.0.0.1。
环境隔离隔离：环境变量 litellm.env 权限必须被严格限制为 600。
证书管理：Caddy 自动处理 HTTPS，不会把 /ui 兜底转发在纯 API 的域名上。
凭证轮换机制：若 LITELLM_MASTER_KEY / SALT_KEY 曾被输出到终端或聊天记录，请立即轮换，不要推入代码仓。
12. 面向 OpenClaw / XWorkmate 的接入说明
IMPORTANT

推荐上层应用接入使用通过 Admin UI 生成的 Virtual Key，不要在业务代码长期硬编码 Master Key！

OpenAI-Compatible 接入 (以 DeepSeek/GPT 为例)

Base URL: https://api.svc.plus/v1/openai
API Key: <LiteLLM Virtual Key>
Model Name: deepseek-chat 或 gpt-5.5
Anthropic-Compatible 接入 (以 Claude 为例)

Base URL: https://api.svc.plus/v1/anthropic
API Key: <LiteLLM Virtual Key>
Model Name: claude-sonnet
Endpoint: POST /messages
未来接入新家族 (如 MiniMax 等)，您只需要在 LiteLLM 层面的 config.yaml 或者 Admin UI 中更新模型列表映射关系即可，上层业务侧的模型稳定别名无需改变。
