# Case: console 域 `/api/auth/*` 被劫持到 accounts，导致 MFA 绑定面板密钥/二维码空白

| | |
|---|---|
| **日期** | 2026-07-31 |
| **环境** | UAT（`console-uat.onwalk.net` / `accounts-uat.onwalk.net`，Vultr 单机 Compose + Doco-CD） |
| **入口现象** | `https://console-uat.onwalk.net/panel/account?setupMfa=1` 弹窗第 2 步「密钥」框空白、二维码不渲染 |
| **实际影响** | UAT 上 MFA 绑定的四个端点**全部**不可用，功能 100% 不可完成 |
| **根因归属** | `playbooks` — `roles/vhosts/web_saas_host_config/templates/Caddyfile.j2` |
| **连带修复** | `ai-workspace-services/portal` — `src/app/api/auth/mfa/{verify,setup}/route.ts` |
| **状态** | 已修复 |

---

## 1. TL;DR

Caddy 在 console 域下把 `/api/auth/*` 整个前缀反代到了 Go 版 accounts 服务，
于是 console 自己的 **Next BFF 路由（`src/app/api/auth/**`）永远收不到请求**。

BFF 不是可有可无的一层转发，它做两件 Go 服务做不到的事：

1. 把浏览器的 `xc_session` **cookie 翻译成 `Authorization: Bearer`**
   （accounts 的 `extractToken()` 只读 header，没有 cookie 回退）；
2. 把 **BFF 路径翻译成 Go 路径**（`/mfa/setup` → `/mfa/totp/provision`）。

这一层被旁路后，前端调 `/api/auth/mfa/setup` 拿到的是 **Gin 的 404**，
`secret` / `otpauth_url` 全是空串 → 密钥框空白、二维码整块不渲染。

> **一句话判别**：console 域下某个 `/api/auth/...` 返回 `content-type: text/plain`
> 的 `404 page not found`，那是 **Gin 的默认 404**，不是 Next 的 —— 说明请求
> 压根没进 console 容器。

---

## 2. 现象

`/panel/account?setupMfa=1` 打开绑定弹窗后：

- 第 2 步「使用 Google Authenticator 身份验证器获取验证码」下面的**密钥框是空的**；
- **二维码不显示**（前端是 `{qrCodeDataUrl ? <Image .../> : null}`，`uri` 为空就整块不渲染）；
- 页面上没有醒目的报错——错误文案渲染在第 3 步卡片下方，首屏往往被截掉，
  且因为响应体不是 JSON，走的是最泛化的 `copy.error` 文案，信息量为零。

容易被误导的两个方向（都已排除）：

- ❌ 「密钥丢了/过期了」——DB 里 `mfa_totp_secret` 全是 NULL，**从来没生成过**；
- ❌ 「前端 pending 态门禁挡住了自动 provision」——前端确实有
  `!hasPendingMfa` 这个门禁，但 `status` 请求本身也是 401，`status` 恒为 null，
  门禁不成立，provision **有**发出去。

---

## 3. 排查过程（证据链）

### 3.1 前端确认调用点

`portal/src/modules/extensions/builtin/user-center/account/MfaSetupPanel.tsx:231`

```ts
const response = await fetch('/api/auth/mfa/setup', { method: 'POST', credentials: 'include', ... })
```

失败分支：响应不是 JSON → `response.json().catch(() => ({}))` 得到 `{}` →
`payload.success` 为 `undefined` → `setError(resolveErrorMessage(undefined))`
→ 落到泛化文案，`secret`/`uri` 保持空串。**症状与代码完全对得上。**

### 3.2 线上直接打这个端点

```bash
curl -sS -i -X POST https://console-uat.onwalk.net/api/auth/mfa/setup \
  -H 'Content-Type: application/json' -d '{}'
```

```
HTTP/2 404
content-type: text/plain
404 page not found
```

`404 page not found` + `text/plain` 是 **Gin 的默认 404**。Next 的 404 是 HTML 或 JSON。
→ 请求根本没到 console 容器。

### 3.3 两侧日志互证

```bash
ssh root@<console-uat-ip> 'docker logs --since 2h web-saas-accounts 2>&1 | grep -iE "mfa|totp"'
ssh root@<console-uat-ip> 'docker logs --since 3h web-saas-console 2>&1 | tail -80'
```

- accounts 里只有 `/api/auth/mfa/status` 的访问记录，**没有任何
  `/api/auth/mfa/totp/provision`**；
- console 里没有 `Account service MFA setup proxy failed`（BFF 的 catch 日志）。

两边都没执行 → 请求死在**路由层**，不是任何一侧的业务逻辑。

### 3.4 DB 佐证

```bash
ssh root@<console-uat-ip> \
  "docker exec web-saas-postgresql psql -U postgres -d account -A -t \
   -c 'select mfa_enabled, mfa_totp_secret is not null as has_secret, count(*) from users group by 1,2;'"
```

```
f|f|2
```

`provisionTOTP` 是先 `UpdateUser` 落库再返回的，密钥全 NULL ⇒ **provision 一次都没成功过**。

### 3.5 看 Caddy 配置——找到真凶

```bash
ssh root@<console-uat-ip> 'docker exec web-saas-caddy sh -lc "cat /etc/caddy/Caddyfile"'
```

```caddyfile
@console_auth {
    host console-uat.onwalk.net
    path /api/auth/*
}
handle @console_auth {
    reverse_proxy accounts:8080 { header_up X-Forwarded-Host {host} }
}
```

来源：`playbooks/roles/vhosts/web_saas_host_config/templates/Caddyfile.j2`
（DNS-01 与 HTTP-01 **两个分支里都有**）。

### 3.6 绕开 Caddy 反证 BFF 是活的

```bash
ssh root@<console-uat-ip> \
  'docker exec web-saas-caddy wget -S -qO /dev/null --post-data="{}" \
   --header="Content-Type: application/json" http://console:3000/api/auth/mfa/setup'
```

```
HTTP/1.1 400 Bad Request      # = BFF 的 mfa_token_required（没带 cookie），JSON 响应
```

Next 路由存在且正常，**只是外部打不到**。定性完成。

---

## 4. 根因

`Caddyfile.j2` 把 `/api/auth/*` 当成了「accounts 的 API 前缀」，
但在 console 域下它是「Next 的 BFF 前缀」。两者同名，语义完全不同。

结果是 MFA 面板的四个端点**全废**，且失效方式各不相同，看起来像四个独立故障：

| 前端调用 | 被路由到 | 结果 |
|---|---|---|
| `GET /api/auth/mfa/status` | Gin `mfaStatus` | **401** — 只读 `Authorization` 头，浏览器只有 cookie |
| `POST /api/auth/mfa/setup` | Gin 无此路由 | **404** ← 报障入口 |
| `POST /api/auth/mfa/verify` | Gin `verifyMFALogin` | 语义错：要 `mfa_ticket`，且断言 `MFAEnabled` 已为 true |
| `POST /api/auth/mfa/disable` | Gin `disableMFA` | **401** `missing authorization header` |

`accounts-uat.onwalk.net` 那个 vhost 本来就直连 accounts，console 的 BFF
通过 `authUrl`（`portal/src/config/runtime-service-config.uat.yaml`）出去回来即可，
**console 域下不需要这条捷径**。

---

## 5. 修复过程中暴露的两个次生缺陷

去掉 Caddy 那条规则后，绑定流程会立刻在下一步撞上这两个（此前被 404 完全掩盖）：

### 缺陷 ②：BFF `mfa/verify` 打错了后端

`portal/src/app/api/auth/mfa/verify/route.ts` 原本请求
`${ACCOUNT_API_BASE}/mfa/verify`，传 `{ mfaToken, code }`。

但 accounts 有**两条**语义不同的 verify：

| 端点 | 用途 | 入参 | 前置断言 |
|---|---|---|---|
| `/mfa/verify` (`verifyMFALogin`) | 登录期二次校验 | `mfa_ticket` / `mfaToken` | **要求 `user.MFAEnabled` 已为 true** |
| `/mfa/totp/verify` (`verifyTOTP`) | **首次绑定确认** | `token` | 无（校验通过后才置 true） |

首次绑定打前者必然返回 `mfa_not_enabled`。已改为打 `/mfa/totp/verify`、字段名改 `token`。
后者的成功响应 `{ message, token, expiresAt, user }` 与该路由既有的
`applySessionCookie(data.token, ...)` 逻辑正好对得上，无需其他改动。

（登录流程的 MFA 是 `/api/auth/login` 自带 totp 字段完成的，不经过这条路由，
所以改它不影响登录。）

### 缺陷 ③：陈旧的 `xc_mfa_challenge` cookie 会把已登录用户判成会话过期

`provisionTOTP` 里 **`token` 优先于 session**：只要请求体带了 `token`，
就走 `refreshMFAChallenge()` 分支，挑战对不上直接 401 `invalid_mfa_token`，
**根本不看 `Authorization`**。

而 MFA 挑战是 accounts 的**进程内存态**（`h.mfaChallenges`），容器一重启全部作废，
浏览器里那个 10 分钟 TTL 的 `xc_mfa_challenge` cookie 却还在。
于是「明明登录着，却报会话过期 / 密钥框空白」——**和本 case 的入口现象一模一样**，
排障时极易混淆。

已在 BFF `setup` 路由改为：**有 session 时不回传陈旧挑战 token**
（登录期 `needMfa` 那条路径尚无 session，仍靠它认身份，不受影响）。

---

## 6. 修复内容

### playbooks

`roles/vhosts/web_saas_host_config/templates/Caddyfile.j2` —— 删掉
`@console_auth` 分流（DNS-01 与 HTTP-01 两个分支），console 域下 `/api/auth/*`
回到 `console:3000`；`accounts-uat` 的 vhost 保持直连不动。

### portal

- `src/app/api/auth/mfa/verify/route.ts`：上游改 `/mfa/totp/verify`，字段名 `mfaToken` → `token`，
  并补上 `Authorization: Bearer <xc_session>`（该端点挂在 `authProtected` 组下）。
- `src/app/api/auth/mfa/setup/route.ts`：有 session 时不回传陈旧挑战 token。
- 新增回归测试 `mfa/verify/route.test.ts`、`mfa/setup/route.test.ts`。

---

## 7. 影响面评估（为什么删规则是安全的）

| 关注点 | 结论 |
|---|---|
| **OAuth 回调** | 不受影响。回调地址固定在 **accounts 域**：`web_saas_host_config/templates/account.yaml.j2:24,28` 写的是 `https://<accounts-domain>/api/auth/oauth/callback/<provider>`；console 的 `oauth/login/[provider]` 路由只是 307 跳到 accounts 域。 |
| **`/api/auth/sync/config`** | 这是唯一没有 BFF 对应路由的调用（VLESS 节点列表）。`fetchAgentNodes.ts` 的 `shouldFallback()` 明确把 **404 列为可回退**，会自动降级到 `/api/agent/nodes`（BFF 有这条）。 |
| **登录** | `/api/auth/login` 从 Gin 移回 BFF。两侧 cookie 名同为 `xc_session`，BFF 的 `applySessionCookie` / `applyMfaCookie` 齐全，且它比 Gin 多处理了 `needMfa` 分支。**这是本次改动爆炸半径最大的一处，上线后必须实测登录。** |
| **`/api/auth/admin/*`** | console 前端走的是 `/api/admin/*`（另一套 BFF 路由），不经过 `/api/auth/admin/*`。 |

---

## 8. 验证清单

### 8.1 配置层

```bash
# 主机上确认新 Caddyfile 已渲染且不含 console_auth
ssh root@<console-uat-ip> 'grep -c console_auth /etc/xcontrol/web-saas/Caddyfile'   # 期望 0
ssh root@<console-uat-ip> 'docker exec web-saas-caddy caddy validate --config /etc/caddy/Caddyfile'
```

### 8.2 路由层（不需要登录态就能判）

```bash
curl -sS -o /dev/null -w '%{http_code} %{content_type}\n' \
  -X POST https://console-uat.onwalk.net/api/auth/mfa/setup \
  -H 'Content-Type: application/json' -d '{}'
```

- 修复前：`404 text/plain`（Gin）
- 修复后：`400 application/json`（BFF 的 `mfa_token_required`）← **这就是判据**

### 8.3 功能层（浏览器，需要登录态）

1. 登录 `console-uat.onwalk.net` —— **回归重点**，登录已从 Gin 移回 BFF；
2. `/panel/account?setupMfa=1` → 密钥框有值、二维码渲染出来；
3. 验证器扫码 → 输 6 位码 → 「验证并启用」→ 弹窗关闭、状态变「已启用」；
4. 后端落库确认：

```bash
ssh root@<console-uat-ip> \
  "docker exec web-saas-postgresql psql -U postgres -d account -A -t \
   -c 'select mfa_enabled, mfa_totp_secret is not null, count(*) from users group by 1,2;'"
# 期望出现 t|t|1
```

5. 退出重登，确认登录页要求输入动态码且校验通过。

### 8.4 回归

- 账户页 VLESS 节点列表仍能加载（`/api/auth/sync/config` 404 → 回退 `/api/agent/nodes`）；
- OAuth 登录（GitHub / Google）跳转与回调正常。

---

## 9. 回滚

改动只在 Caddyfile（主机侧配置）与 console 镜像。回滚 Caddy 侧：

```bash
# 把 @console_auth 块加回 /etc/xcontrol/web-saas/Caddyfile 后
ssh root@<console-uat-ip> 'docker exec web-saas-caddy caddy reload --config /etc/caddy/Caddyfile --force'
```

console 侧回滚按 gitops 的镜像 tag 契约走（见 `platform-ops-toolkit/docs/domains/IMAGE-TAG-CONTRACT.md`），
**不要**直接在主机上改 tag。

---

## 10. 预防

1. **`/api/auth/*` 在 console 域下属于 console**。要给 accounts 开路径，用
   accounts 自己的域名，不要在 console 域下开前缀捷径——两边路径空间同名不同义。
2. **反代新增/修改路径分流后，必须验一条"该前缀下 BFF 独有的路由"**，
   而不只是验站点首页 200。本 case 里首页、登录、账户页全部正常，只有 BFF
   独有的那几条是死的。
3. **`404 page not found` + `text/plain` = Gin**。在一个 Next 站点的域名下看到它，
   第一反应应该是"反代把请求送错了地方"，而不是"前端路径写错了"。
4. **进程内存态的挑战/会话要考虑容器重启**：cookie 活着而服务端状态没了，
   表现为莫名其妙的"会话过期"。凡是"token 优先于 session"的后端分支，
   BFF 侧都应在有 session 时避免回传可能已失效的 token。

---

## 11. 相关文件索引

| 文件 | 作用 |
|---|---|
| `playbooks/roles/vhosts/web_saas_host_config/templates/Caddyfile.j2` | 根因所在；渲染到主机 `/etc/xcontrol/web-saas/Caddyfile`，挂载进 `web-saas-caddy` |
| `playbooks/roles/vhosts/web_saas_host_config/templates/account.yaml.j2` | OAuth 回调地址（固定在 accounts 域） |
| `portal/src/app/api/auth/mfa/{setup,verify,status,disable}/route.ts` | console BFF 的 MFA 四个端点 |
| `portal/src/modules/extensions/builtin/user-center/account/MfaSetupPanel.tsx` | 绑定弹窗前端 |
| `portal/src/config/runtime-service-config.uat.yaml` | `authUrl` —— BFF 访问 accounts 的出口 |
| `portal/src/server/serviceConfig.ts` | `getAccountServiceApiBaseUrl()` = `<authUrl>/api/auth` |
| `accounts/api/api.go:379-396` | `authProtected` 组与 MFA 路由定义 |
| `accounts/api/api.go:2127` `provisionTOTP` / `:2319` `verifyTOTP` / `:1304` `verifyMFALogin` | 三个后端处理器 |
