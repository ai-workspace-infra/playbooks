# MCP Forward Auth 与统一身份集成规划

> Status: 🟡 Planning
>
> Date: 2026-08-15
>
> Related Issue: [#360 — 需求收集：MCP Forward Auth 与 Zitadel 多租户统一认证](https://github.com/ai-workspace-infra/playbooks/issues/360)
>
> Related PRs: [#361 — docs: plan MCP forward auth integration](https://github.com/ai-workspace-infra/playbooks/pull/361) [DRAFT]
> Domains: `open-platform`（Zitadel、Observability、MCP）、`web-saas`（Accounts 租户映射）

## 目标

将 `observability.svc.plus/mcp/*` 从共享 Basic Auth 迁移到基于 Zitadel JWT 的统一认证与细粒度授权，同时保持组件职责清晰：

- Zitadel 负责身份、JWT、组织、Project、Role 和密钥轮换。
- `mcp-auth` 负责 MCP 专属策略校验、租户上下文解析和审计决策。
- `accounts.svc.plus` 负责业务账户与租户映射，不提供通用网关鉴权接口。
- Caddy 只通过 `forward_auth` 委托鉴权并转发请求，不解析 JWT、不维护授权规则。

本计划遵循领域交付清单：Zitadel、Observability 和 MCP 属于 `open-platform`；Accounts 属于 `web-saas`。跨域只通过稳定 API 契约协作，不把单个服务部署步骤塞入平台编排 workflow。

## 目标架构

```text
Codex / MCP Client
  │ Authorization: Bearer <Zitadel access token>
  ▼
observability.svc.plus (Caddy)
  │ forward_auth + original request metadata
  ▼
mcp-auth (iam.svc.plus 内部服务)
  ├─ 校验 Zitadel issuer / signature / aud / exp / nbf
  ├─ 校验 Project Role 与 MCP scope
  ├─ 解析 organization / tenant context
  ├─ 必要时查询 Accounts 租户映射 API
  └─ 返回 2xx / 401 / 403，并附加可信身份 Header
  ▼
Grafana / VictoriaMetrics / VictoriaLogs / VictoriaTraces MCP
```

## 组件边界

| 组件 | 负责 | 不负责 |
|---|---|---|
| Zitadel | 用户与机器身份、OIDC/OAuth2、JWT、组织、Project、Role、JWKS | MCP 路径级策略、业务租户映射 |
| `mcp-auth` | Token 验证、MCP scope、租户授权、决策审计、可信 Header | 用户登录 UI、业务账户生命周期、MCP 数据查询 |
| Accounts | 业务账户、租户、成员关系和订阅状态的权威映射 | 通用反向代理鉴权、JWT 密钥管理 |
| Caddy | TLS、`forward_auth`、路由、清理外部身份 Header、转发可信 Header | JWT 解析、租户查询、策略判断 |
| MCP adapters | 只读调用 Grafana/Victoria 后端 | 外部身份认证、租户授权 |

## 身份与权限模型

### Zitadel 资源

建议建立独立 Project：`observability-mcp`，并至少定义以下角色：

| Role / Scope | 允许访问 |
|---|---|
| `mcp:grafana:read` | Grafana MCP |
| `mcp:metrics:read` | VictoriaMetrics MCP |
| `mcp:logs:read` | VictoriaLogs MCP |
| `mcp:traces:read` | VictoriaTraces MCP |
| `mcp:admin` | 全部 MCP；仅平台管理员 |

Token 必须包含或可解析：`iss`、`sub`、`aud`、`exp`、`nbf`、`jti`、Organization/Project Role。业务 `tenant_id` 不直接信任客户端自定义 Header，应由 `mcp-auth` 根据 Zitadel Organization 与 Accounts 权威映射解析。

### 客户端类型

- 人员交互：Authorization Code + PKCE，短期 access token。
- Codex/自动化：Machine User 或 Service Account，使用 Client Credentials；每个环境、调用方和权限集独立 client。
- 禁止长期共享 Bearer Token；禁止把 access token 写入仓库或 GitHub Secrets。

## `mcp-auth` API 契约

### 请求

```http
GET /internal/mcp-auth/verify
Authorization: Bearer <token>
X-Original-Method: POST
X-Original-URI: /mcp/v1/logs/mcp
X-Original-Host: observability.svc.plus
```

### 响应

| 状态 | 语义 |
|---|---|
| `2xx` | Token 与目标资源授权均通过 |
| `401` | Token 缺失、无效、过期或 issuer/audience 不匹配 |
| `403` | 身份有效，但租户、角色或 scope 不允许访问目标 MCP |
| `429` | 鉴权调用超限 |
| `5xx` | 鉴权依赖异常；Caddy 必须 fail-closed，不可匿名降级 |

授权成功时只返回经过服务端生成的可信 Header：

```http
X-Auth-Subject: <zitadel-sub>
X-Auth-Organization: <zitadel-org-id>
X-Auth-Tenant: <accounts-tenant-id>
X-Auth-Scopes: mcp:logs:read
X-Auth-Decision-Id: <audit-id>
```

## Caddy 接入约束

每个 MCP 路由独立声明所需资源，并在 `forward_auth` 前删除客户端提交的 `X-Auth-*` Header。示意配置：

```caddy
handle_path /mcp/v1/logs/* {
    request_header -X-Auth-Subject
    request_header -X-Auth-Organization
    request_header -X-Auth-Tenant
    request_header -X-Auth-Scopes
    request_header -X-Auth-Decision-Id

    forward_auth 127.0.0.1:19090 {
        uri /internal/mcp-auth/verify
        header_up Authorization {header.Authorization}
        header_up X-Original-Method {http.request.method}
        header_up X-Original-URI {http.request.uri}
        header_up X-Original-Host {http.request.host}
        header_up X-MCP-Resource logs
        copy_headers X-Auth-Subject X-Auth-Organization X-Auth-Tenant X-Auth-Scopes X-Auth-Decision-Id
    }

    reverse_proxy 127.0.0.1:8083
}
```

最终模板应复用 Caddy snippet，避免四套 MCP 路由复制出不同的安全规则。Caddy 与 `mcp-auth` 优先走本机回环或私有网络，不通过公网 `iam.svc.plus` 绕行。

## Accounts 租户映射契约

`mcp-auth` 可调用 Accounts 的内部只读接口，但该接口只返回租户映射，不返回最终授权决定：

```http
GET /internal/identity-mappings/zitadel/{organization_id}
Authorization: Bearer <service-token>
```

建议返回：`tenant_id`、`status`、`membership_version`。`mcp-auth` 对结果做短 TTL 缓存；Accounts 不可用时，对缓存外的新请求 fail-closed。服务间凭据通过 Vault/OIDC 获取，按 SIT/UAT/PROD 分离。

## Vault 与环境隔离

建议路径：

```text
kv/data/observability/mcp-auth/sit
kv/data/observability/mcp-auth/uat
kv/data/observability/mcp-auth/prod
```

只保存不能由 OIDC 动态获得的配置，例如 Zitadel client ID/secret、Accounts 内部 API 凭据和必要的加密材料。JWT 验证公钥通过 Zitadel JWKS 获取并缓存，不把 JWKS 私钥复制到 `mcp-auth`。

现有 `kv/data/observability/mcp` Basic Auth 凭据在迁移期保留为受控回滚能力；完成验收后撤销并删除，不长期双轨运行。

## 最佳实施时间与阶段

### P0：契约与安全基线（0.5–1 天）

- 明确 Zitadel issuer、audience、Organization 与 Accounts tenant 的映射规则。
- 确定四个 MCP resource 到 scope 的映射。
- 固化 `/internal/mcp-auth/verify` 和 Accounts 映射 API 契约。
- 定义审计字段、错误码、SLO 和 fail-closed 行为。

完成标准：契约评审通过，SIT/UAT/PROD 的 Vault 路径和 Zitadel Project/Role 命名确定。

### P1：`mcp-auth` 最小可用服务（1–2 天）

- 在 `roles/docker/zitadel/` 交付链中增加独立 `mcp-auth` 容器和健康检查；服务逻辑保持独立制品。
- 实现 JWKS 缓存、JWT 校验、audience/scope/path 策略和结构化审计。
- 暂不依赖 Accounts，先使用 Zitadel Organization 完成平台管理员与机器账号授权。

完成标准：单元测试覆盖 2xx/401/403/5xx、JWKS 轮换、过期 Token、错误 audience 与 Header 注入攻击。

### P2：SIT Caddy `forward_auth`（0.5–1 天）

- 在 Observability role 增加可开关的 `forward_auth` 模式。
- 保留 Basic Auth 回滚开关，但单次请求只允许一种认证链路，避免宽松 OR 逻辑。
- 使用 Codex `--bearer-token-env-var` 接入四个 MCP。

完成标准：SIT 四个 MCP 均通过合法 token；缺失 token 为 401、权限不足为 403、鉴权服务不可用时为 503。

### P3：Accounts 多租户映射（1–2 天）

- Accounts 提供内部只读映射 API。
- `mcp-auth` 引入租户映射缓存和 membership version。
- 验证跨租户拒绝、禁用租户拒绝和缓存失效。

完成标准：同一身份只能访问授权租户；伪造 `X-Auth-Tenant` 无效；Accounts 短暂不可用时行为符合策略。

### P4：UAT 灰度与可观测性（1 天，至少观察 24 小时）

- 先给内部机器账号和平台管理员灰度。
- Dashboard 展示认证成功率、401/403/5xx、延迟、JWKS 刷新和 Accounts 映射错误。
- Alert 覆盖鉴权失败突增、P95 延迟和 auth service 不可用。

完成标准：连续 24 小时无未授权放行；P95 鉴权延迟目标小于 50 ms（缓存命中）；MCP 流式请求不被中断。

### P5：PROD 切换与 Basic Auth 退役（0.5 天）

- 在低流量窗口切换 PROD。
- 验证四个 MCP、Codex 客户端、审计日志和租户隔离。
- 撤销并删除旧 Basic Auth 凭据，更新运行手册。

完成标准：Bearer 为唯一入口认证方式；旧密码无法访问；回滚只通过版本化配置，不恢复长期共享密码。

## 测试矩阵

| 场景 | 预期 |
|---|---|
| 无 Authorization | `401` |
| 错误签名或错误 issuer/audience | `401` |
| Token 过期或尚未生效 | `401` |
| 合法身份但缺少目标 MCP scope | `403` |
| 合法 scope 与租户 | MCP 正常响应 |
| 伪造 `X-Auth-*` | Header 被清除并由 auth 服务重建 |
| Accounts 映射不存在或租户禁用 | `403` |
| `mcp-auth` 不可用 | `503`，不转发 MCP |
| JWKS 轮换 | 刷新后新 key 可用，旧 token 按有效期策略处理 |
| 四个 MCP 并发与流式请求 | 认证只发生在建连阶段，不破坏 streamable HTTP |

## 可观测性与审计

- 指标：请求量、允许/拒绝数量、401/403/5xx、延迟、缓存命中率、JWKS 刷新失败、Accounts 查询失败。
- 日志：记录 decision ID、subject、organization、tenant、resource、scope 与拒绝原因；不记录完整 JWT、client secret 或 Authorization Header。
- Trace：Caddy → `mcp-auth` → Accounts 映射 API 使用统一 trace context。
- 告警：鉴权服务不可用、拒绝率异常、跨租户拒绝、P95 延迟超标。

## 回滚策略

1. 每一阶段使用独立 feature flag：`basic_auth`、`forward_auth` 只能明确选择其一。
2. UAT/PROD 回滚通过上一个不可变部署版本恢复 Caddy 与 `mcp-auth`。
3. 回滚期间 Basic Auth 凭据必须来自 Vault 且重新轮换，禁止恢复已暴露密码。
4. `mcp-auth` 故障默认阻断 MCP，不影响 Observability 的 metrics/logs/traces ingest 路径和 Grafana UI。

## 非目标

- 本阶段不把 Grafana UI、遥测写入入口统一迁入 Zitadel。
- 不让 Accounts 成为所有平台服务的通用鉴权代理。
- 不在 Caddy 中编写 JWT 或租户策略。
- 不在平台编排 workflow 中为 `mcp-auth` 增加孤立部署 step；它应通过 `open-platform` 域 CD 交付。

## 后续交付拆分

1. `mcp-auth` 服务制品与测试 PR。
2. Zitadel Project/Role/Client 初始化与 Vault/OIDC PR。
3. Playbooks `roles/docker/zitadel/` 的 `mcp-auth` 部署 PR。
4. Observability Caddy `forward_auth` 与回滚开关 PR。
5. Accounts 内部租户映射 API PR。
6. Codex MCP Bearer 配置与运维文档 PR。
7. SIT → UAT → PROD 分阶段验证记录。
