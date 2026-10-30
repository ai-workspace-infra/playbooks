# obs_agent Ansible Role (Docker / Doco-CD)

本 Role 负责观测云 `obs-agent` 在单机 Docker / Doco-CD 环境下的全生命周期部署与配置管理。

## 核心特性
1. **严格的安全上下文**：落实 `--user 10001:10001`、`--cap-drop ALL`、`--security-opt no-new-privileges=true`。
2. **独立持久化命名卷**：自动创建并独立挂载 `obs-agent-work`、`obs-agent-profile-cache`、`obs-agent-owl`，保障会话与配置不丢失。
3. **安全凭据集成**：遵循 `优雅降级原则`，支持系统环境变量、HashiCorp Vault KV V2 动态获取，敏感信息文件设为 `0600` 且日志脱敏。
4. **启动健康门禁**：自动轮询检验 `obs-agent websocket connected` 日志。
