# Minimal AI API Gateway (Caddy + LiteLLM + PostgreSQL)

这是一个轻量、可控、可扩展的统一外部模型服务接入层。

## 架构说明

```
Caddy + LiteLLM Minimal AI API Gateway + PostgreSQL

├── API Gateway
│   └── https://api.svc.plus
│       ├── POST /v1/openai/chat/completions
│       ├── POST /v1/openai/embeddings
│       ├── POST /v1/anthropic/messages
│       └── GET  /v1/models
│
├── AI Gateway Dashboard
│   └── https://api.svc.plus/ui
│       ├── Models + Endpoints
│       ├── Virtual Keys
│       └── Basic Logs / Usage
│
├── LiteLLM Proxy
│   └── http://127.0.0.1:4000
│
├── PostgreSQL Runtime DB
│   ├── Raw:       127.0.0.1:5432
│   ├── TLS Proxy: 127.0.0.1:15432
│   └── DB:        litellm
│
└── Providers
    └── 默认为空，后续通过 Dashboard 手动添加
```

## 部署说明

### Caddy 配置
Caddy 作为唯一公网 HTTPS 入口，执行路径白名单拦截。
内部映射如下：
- `/ui*` -> `http://127.0.0.1:4000/ui*` (且强制鉴权)
- `/v1/openai/chat/completions` -> `http://127.0.0.1:4000/v1/chat/completions`
- `/v1/openai/embeddings` -> `http://127.0.0.1:4000/v1/embeddings`
- `/v1/anthropic/messages` -> `http://127.0.0.1:4000/v1/messages`
- `/v1/models` -> `http://127.0.0.1:4000/v1/models`
- 未匹配路径返回 `404 Not Found`。

### LiteLLM config.yaml
配置极致精简，不预设任何模型：
```yaml
model_list: []

general_settings:
  master_key: "os.environ/LITELLM_MASTER_KEY"
  database_url: "os.environ/DATABASE_URL"
  store_model_in_db: true
  drop_rate_limit_requests: true

router_settings:
  routing_strategy: simple-shuffle
  num_retries: 2
  retry_after: 30
  fallbacks: []

litellm_settings:
  drop_params: true
  set_verbose: false
  request_timeout: 600
  telemetry: false
```

### litellm.env.example
请复制该文件为 `/etc/litellm/litellm.env`，权限必须保持为 600。
```env
LITELLM_MASTER_KEY=
LITELLM_SALT_KEY=
LITELLM_DB_PASSWORD=
DATABASE_URL=postgresql://litellm:replace-with-strong-password@127.0.0.1:15432/litellm?sslmode=require

OPENAI_API_KEY=
ANTHROPIC_API_KEY=
DEEPSEEK_API_KEY=
MINIMAX_API_KEY=
LOCAL_MODEL_API_KEY=
VOYAGE_API_KEY=
JINA_API_KEY=
```

### systemd 服务
`/etc/systemd/system/litellm-proxy.service`
监听地址：`127.0.0.1:4000`
依赖项：`network-online.target` 和 `stunnel-postgres-client.service`。

### PostgreSQL / stunnel 连接说明
- 裸连接: `127.0.0.1:5432`
- TLS 加密连接 (LiteLLM 使用): `127.0.0.1:15432`
仅用作基础持久化（Models, Virtual Keys, Basic Logs / Usage）。

### Dashboard 使用边界
Dashboard (`https://api.svc.plus/ui`) 仅用于：
- Models + Endpoints
- Virtual Keys
- Basic Logs / Usage

### 不启用功能清单
明确禁用复杂多租户治理功能，包括但不限于：
- Teams, Organizations, Budgets
- Guardrails, MCP Servers, Skills, Policies
- 复杂审计、用户与权限体系

---

## 接口兼容说明

### OpenAI-Compatible 接口
Base URL: `https://api.svc.plus/v1/openai`
对外接口：
- `POST /chat/completions` (Chat 专用)
- `POST /embeddings` (Embedding 专用)

### Anthropic-Compatible 接口
Base URL: `https://api.svc.plus/v1/anthropic`
对外接口：
- `POST /messages`

### Embeddings 接口兼容说明
统一收敛至 `https://api.svc.plus/v1/openai/embeddings`。

---

## 客户端接入说明

### Claude Code 接入
```bash
export ANTHROPIC_BASE_URL=https://api.svc.plus/v1/anthropic
export ANTHROPIC_AUTH_TOKEN=sk-your-virtual-key
# model 推荐 claude-sonnet
```

### OpenAI SDK / Agent 接入 (OpenClaw, XWorkmate)
```bash
export OPENAI_BASE_URL=https://api.svc.plus/v1/openai
export OPENAI_API_KEY=sk-your-virtual-key
# model: chat-default / embedding-default
```

---

## 验证命令

1. 本地健康检查
```bash
curl http://127.0.0.1:4000/health
```

2. Model Catalog
```bash
curl -H "Authorization: Bearer $LITELLM_MASTER_KEY" https://api.svc.plus/v1/models
```

3. OpenAI-Compatible Chat
```bash
curl -X POST "https://api.svc.plus/v1/openai/chat/completions" \
-H "Authorization: Bearer $LITELLM_MASTER_KEY" \
-H "Content-Type: application/json" \
-d '{"model": "chat-default", "messages": [{"role": "user", "content": "Hello from OpenAI-compatible endpoint"}], "stream": false}'
```

4. OpenAI-Compatible Embeddings
```bash
curl -X POST "https://api.svc.plus/v1/openai/embeddings" \
-H "Authorization: Bearer $LITELLM_MASTER_KEY" \
-H "Content-Type: application/json" \
-d '{"model": "embedding-default", "input": "AI API Gateway embedding test"}'
```

5. Anthropic-Compatible Messages
```bash
curl -X POST "https://api.svc.plus/v1/anthropic/messages" \
-H "Authorization: Bearer $LITELLM_MASTER_KEY" \
-H "Content-Type: application/json" \
-H "anthropic-version: 2023-06-01" \
-d '{"model": "claude-sonnet", "max_tokens": 256, "messages": [{"role": "user", "content": "Hello from Anthropic-compatible endpoint"}], "stream": false}'
```

## 安全注意事项
- Providers 默认为空，模型配置不在代码中预设，避免 API Key 泄露。
- 如果任何密钥出现在日志/截图/代码库中，请立刻轮换！
