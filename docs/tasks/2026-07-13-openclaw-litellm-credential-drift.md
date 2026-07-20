# OpenClaw LiteLLM 凭据漂移排障记录

**状态：** 已手工恢复，自动化修复待实现  
**日期：** 2026-07-13  
**关联 PR：** 无

## 现象

OpenClaw Gateway、LiteLLM 和 XWorkMate Bridge 都在运行，`127.0.0.1:4000` 也可访问；但 OpenClaw 每次生成回复前失败。Gateway 日志显示 LiteLLM 返回 `401 Invalid proxy server token`。

## 根因

LiteLLM 的本地 OpenAI 兼容端点 `http://127.0.0.1:4000/v1` 只接受统一的 LiteLLM virtual key，即服务用户的 `~/.ai_workspace_auth_token`。

部署模板本身已正确渲染该 key：`gateway_openclaw_provider_deepseek.apiKey` 取自 `ai_workspace_auth_token`。但角色只部署 `~/.openclaw/openclaw.json`，不会校验或迁移已有的 `~/.openclaw/agents/main/agent/auth-profiles.json`。遗留 profile 保留了原始 `DEEPSEEK_API_KEY`；OpenClaw 运行时优先选中该 profile，因而把上游 DeepSeek key 发送给 LiteLLM，得到 401。

原始 `DEEPSEEK_API_KEY` 直连 DeepSeek 上游是有效的；它不能充当 LiteLLM virtual key。`GET /v1` 返回 404 也符合 OpenAI 兼容 API 的行为，实际应请求如 `/v1/models` 或 `/v1/chat/completions`。

## 手工恢复

1. 备份 `~/.openclaw/agents/main/agent/auth-profiles.json`。
2. 将其中用于 LiteLLM provider 的旧原始 DeepSeek key 替换为 `~/.ai_workspace_auth_token` 的内容。
3. 重启 `openclaw-gateway` 用户服务。
4. 验证 `/v1/models` 与一次最小 `deepseek/deepseek-v4-flash` chat completion 都返回 200。

## 防复发

- OpenClaw 的 `baseUrl` 指向本机 LiteLLM `/v1` 时，provider/profile 的 `apiKey` 必须统一读取 `~/.ai_workspace_auth_token`，不得写入上游 provider key。
- 一键部署应在渲染 OpenClaw 配置后，对已有 `auth-profiles.json` 做受控迁移或失效处理；同时创建部署后 smoke test，使用该 token 调用 `/v1/models` 和默认模型。
- 需要保留原始 `DEEPSEEK_API_KEY` 时，仅将其配置给 LiteLLM 作为上游 provider 凭据，不能传给 OpenClaw 的 LiteLLM endpoint。
