# obs_agent Kubernetes Helm Role (`roles/charts/obs_agent`)

本 Role 负责使用官方 Helm Chart 在 Kubernetes 集群中部署 `obs-agent`。

## 规范实现
1. **持久化 PVC**：遵循官方指导创建三个卷：`work (20Gi)`、`profileCache (5Gi)`、`owl (5Gi)`。
2. **安全隔离**：
   - `automountServiceAccountToken: false`（无需 K8s API token）
   - `securityContext`: `runAsUser: 10001`、`runAsGroup: 10001`、`allowPrivilegeEscalation: false`、`seccompProfile.type: RuntimeDefault`、`capabilities.drop: [ALL]`。
3. **Secret 隔离**：使用 `secret.existingSecret: obs-agent-secret`，解耦敏感参数。
4. **集成 Vault**：自动从 Vault KV2 提取 `AGENT_ID` 与 `AGENT_API_KEY` 生成 K8s Secret。
