# web_saas_host_config

渲染 web-saas compose 栈的**主机侧输入**:机密、配置、TLS 证书、Caddyfile。

不部署容器。compose 文件本身在
[`ai-workspace-infra/gitops`](https://github.com/ai-workspace-infra/gitops) 的
`compose/web-saas/`,由 Doco-CD 拉取。

新候选 owner 可显式设置 `web_saas_host_config_candidate_mode: true`，只渲染声明的
Console/Accounts 别名；全新目标不拦截登录等写请求；`web_saas_host_config_enforce_alias_pattern: false`
允许声明任意目标别名。两个开关的默认值保持既有行为，且不隐含 DNS、ACME 或切流授权。
候选入口的 Vault 引用策略和运行时解析见 `docs/selfhost-control-plane-contract.md`。

## 为什么这样切

判据是**谁来管版本**:

| | 归属 | 版本记录 |
|---|---|---|
| 镜像 tag | gitops 仓 `compose/web-saas/.env.<env>` | `git log` |
| 口令、证书、配置 | 本角色 ← Vault | Vault 版本 |

混在一起就会出现「改个镜像 tag 要动证书」,或者反过来「轮换一次证书触发一次
业务发布」。

## 落盘位置

```
/etc/xcontrol/web-saas/
├── secrets.env          0600  各服务以绝对路径 env_file 读取
├── Caddyfile            0644  compose 里那个 caddy 容器用的, 不是主机 systemd caddy
├── config/              0755  postgresql.conf / stunnel-{server,client}.conf / account.yaml
└── certs/               0700  ca-cert.pem / server-cert.pem / server-key.pem(0600)
```

compose 里这些路径**必须是绝对的**:Doco-CD 每次部署都把 gitops 仓全新 clone
到临时目录,相对路径会解析到那个 clone 里 —— 证书并不在那儿,于是 docker
挂载出一个空目录**而不是报错**。

## 输入

全部经环境变量注入,由流水线从 Vault OIDC 取得。角色自己不连 Vault,仓库里
也不保存任何字面值。

**必需**(缺失即失败,见下):

- `WEB_SAAS_POSTGRES_PASSWORD`、`ACCOUNT_PG_PASSWORD`
- `AUTH_TOKEN_PUBLIC_TOKEN`、`AUTH_TOKEN_REFRESH_SECRET`、`AUTH_TOKEN_ACCESS_SECRET`
- `WEB_SAAS_STUNNEL_{CA_CERT,SERVER_CERT,SERVER_KEY}_B64`(base64,避免换行在 env 里被破坏)
- `WEB_SAAS_CONSOLE_DOMAIN`、`WEB_SAAS_ACCOUNTS_DOMAIN`、`TARGET_DOMAIN_BASE`、`DEPLOY_ENV`
- `WEB_SAAS_CONSOLE_PUBLIC_DOMAIN`、`WEB_SAAS_ACCOUNTS_PUBLIC_DOMAIN`（可选；用于
  V2+ 主机继续承载既有公共域名，默认仍为
  `console-<env>.<base>` / `accounts-<env>.<base>`）
- `ROOT_BOOTSTRAP_EMAIL`、`ROOT_BOOTSTRAP_PASSWORD`（Accounts 首次 root 引导凭据，均由 Vault 注入）

`TARGET_DOMAIN_BASE` 是部署和迁移共用的唯一 base-domain 输入。它同时决定
Accounts 收到的共享租户 host（例如 `onwalk.net`）以及 VPS selfhost 别名的后缀；
例如 `DEPLOY_ENV=uat` 时，VPS Caddy 地址是 `accounts-selfhost-uat.onwalk.net`，
公共地址 `accounts-uat.onwalk.net` 由 DNS 指向该 selfhost 目标；由于这是
DNS-only，Caddy 必须同时接受这两个 Host。Caddy 转发给 Accounts 的
`X-Forwarded-Host` 则是 `onwalk.net`。不要传入
`source_domain_base`，也不要在 role 或 Caddyfile 中硬编码域名。

生产 `web-saas-prod` 的 Caddy 同时接受 selfhost 与 canonical Host：

| 服务 | selfhost | canonical | Caddy 行为 |
|---|---|---|---|
| Console | `console-selfhost-prod.svc.plus` | `console.svc.plus` | 反代 `console:3000` |
| Accounts | `accounts-selfhost-prod.svc.plus` | `accounts.svc.plus` | 反代 `accounts:8080` |
| Billing ingest | `billing-selfhost-prod.svc.plus` | `billing.svc.plus` | 仅在 agent CIDR 已配置时反代 `billing:8081` |
| PostgreSQL | `postgresql-selfhost-prod.svc.plus` | `postgresql.svc.plus` | 仅作为服务/DNS 身份，不经过 HTTP Caddy、不开放公网 5432 |

生产还将 `xworktech.com` 和 `selfhost-prod.xworktech.com` 作为 Console 镜像的品牌入口。
`WEB_SAAS_BRAND_SELFHOST_DOMAIN` 为不切换正式品牌域名即可验证源站提供入口。
这些域名使用独立 Caddy
site block，因为 `*.svc.plus` 证书不覆盖 `xworktech.com`；启用 Cloudflare DNS-01
时单独签发，否则使用 DNS 已切到该主机后的 HTTP-01。品牌域名仍由 DNS/IaC 决定，
本 role 不修改 DNS。

**可选的跨节点 Billing 入口**:

- `WEB_SAAS_BILLING_DOMAIN`：例如 `billing-uat.onwalk.net`。
- `WEB_SAAS_BILLING_ALLOWED_CIDRS`：允许触发 Billing job 的 agent-proxy 源地址，
  例如 `167.179.64.91/32`。

PROD 渲染时，角色会把经核验的六个 V3 Accounts Proxy 入口地址追加到此列表，
并与流水线传入的 CMDB CIDR 去重合并。当前地址对应
`jp-free-selfhost-prod-v2.svc.plus`、`jp-xconnect-01.svc.plus`、
`sg-free-selfhost-prod-v2.svc.plus`、`us-free-selfhost-prod-v2.svc.plus`、
`jp-xconnect.svc.plus` 和 `ph-xconnect.svc.plus`；节点 DNS 地址变化时，
需先复核并更新角色默认值，再发布 Caddy 配置。UAT 不追加这组 PROD 地址，
仍使用 UAT 专属 CIDR override。

只有这两个值同时非空时，Caddy 才会渲染 Billing 站点；否则不会把
`/v1/jobs/*` 暴露到公网。agent-proxy 的 `billing.baseURL` 必须使用该 HTTPS 域名，
而不是 web-saas 容器内的 `127.0.0.1:8081`。Billing 仍然负责从 Exporter 读取快照并
独占写入共用 PostgreSQL，Caddy 只承载 agent 的调度触发请求。
- `EXPORTER_SOURCES_JSON`：Billing 采集的一个或多个远端 xray-exporter 来源；必须使用
  可从 web-saas 主机访问的地址，不能填 exporter 所在主机上的 `127.0.0.1`

Exporter 运行时默认下载线上兼容基线 `compassvpn/xray-exporter:v0.6.0`。
UAT 验证自定义构建时，可通过 `XRAY_EXPORTER_RELEASE_REPOSITORY` 和
`XRAY_EXPORTER_VERSION` 覆盖发布仓库与 tag；未设置时不改变现有生产下载路径。

启用 Billing snapshot 时，Exporter 通过 `INTERNAL_SERVICE_TOKEN`、
`ACCOUNTS_BASE_URL` 和 `EXPORTER_NODE_ID` 从 Accounts 做身份映射，并在同一
HTTP 服务上提供 Bearer 保护的 `/v1/snapshots/*`。Prometheus 的 `/scrape`
路径保持原有行为，供 Vector/Observability/Grafana 使用。

**可选**:`EXPORTER_BASE_URL`（仅旧单源兼容）、`BILLING_DB_PASSWORD`、`OAUTH_{GITHUB,GOOGLE}_CLIENT_{ID,SECRET}`、
`INTERNAL_SERVICE_TOKEN`

可选键缺失不会让部署失败(见 `platform-ops-toolkit`
`.github/scripts/platform-ops_deploy_base_load-optional-web-saas.sh` ——
`vault-action` 的 `ignoreNotFound` 只覆盖整个 KV 路径 404,覆盖不了"路径存在、
单个键缺失"这种情况,所以可选键必须单独读取、缺则置空)。缺失时对应的登录方式
在 accounts 服务里不可用,其余功能不受影响。

### 接入 GitHub OAuth

1. **GitHub → Settings → Developer settings → OAuth Apps**
   (`https://github.com/settings/developers`),为 accounts 服务新建一个 OAuth App
   (或使用组织下已有的)。
2. **Authorization callback URL** 填
   `https://<accounts-domain>/api/auth/oauth/callback/github`
   (对应 `roles/vhosts/web_saas_host_config/templates/account.yaml.j2` 里
   `oauth.github.redirectUrl` 的取值)。
3. **Client ID** 在 App 详情页明文可见;**Client Secret** 只有创建/重新生成的
   那一刻能看到明文,务必当场取走。
4. 写入 `kv/data/WEB_SAAS`:键名 `OAUTH_GITHUB_CLIENT_ID`、
   `OAUTH_GITHUB_CLIENT_SECRET`。

### 接入 Google OAuth

1. **Google Cloud Console → APIs & Services → Credentials**
   (`https://console.cloud.google.com/apis/credentials`),选择项目后
   **Create Credentials → OAuth client ID**,应用类型选 **Web application**。
2. 首次配置该项目的 OAuth 同意屏幕(**OAuth consent screen**)时,需要
   Authorized domain(填 `TARGET_DOMAIN_BASE` 对应的根域名,例如
   `onwalk.net`)。
3. **Authorized redirect URIs** 填
   `https://<accounts-domain>/api/auth/oauth/callback/google`
   (对应 `oauth.google.redirectUrl`)。
4. **Client ID** 在凭据列表明文可见;**Client secret** 同样只有创建时能看到
   明文,或点击凭据详情里的显示/重置。
5. 写入 `kv/data/WEB_SAAS`:键名 `OAUTH_GOOGLE_CLIENT_ID`、
   `OAUTH_GOOGLE_CLIENT_SECRET`。

> 两者的回调域名都是 `WEB_SAAS_ACCOUNTS_DOMAIN`(例如 UAT 下是
> `accounts-selfhost-uat.onwalk.net`),不是 console 的域名——OAuth 回调固定落在
> accounts 服务上,console 只是发起登录跳转的一方。

## 断言在最前面,这是有意的

空口令**不会让任何东西崩溃**:postgres 带着空口令正常启动,accounts 正常监听,
部署报绿,直到有人第一次尝试登录才发现。证书缺失同理——docker 会挂载一个空
目录而不是失败。

所以这些检查必须在写任何文件之前跑,并且必须让 play 失败。

控制平面从固定 stunnel-server 镜像读取实际 UID/GID，显式传入
`web_saas_host_config_stunnel_key_group` 和 `web_saas_host_config_stunnel_key_mode`。
非 root 镜像使用 root:<镜像 GID> 0640；既有调用默认仍为 root:root 0600。
证书目录保持 0700，Compose 单文件只读挂载，私钥不开放给其他用户。
候选与公共域名相同时，Caddy host matcher 去重后渲染。

候选控制平面显式启用 `web_saas_host_config_stunnel_use_system_trust`，将主机
受管理的系统根证书追加到 Vault 提供的 CA 链，并在发布前验证完整链和目标主机名。
stunnel 保持 `verifyChain = yes`，同时设置 `checkHost`，不接受未知 CA 或错误域名。
默认不开启此选项，保留既有调用的私有 CA 行为。

全新目标的业务写请求由应用正常处理。迁移上游的数据只读约束属于源端迁移角色，
不能用候选域名或 fresh-install 标志对目标全局阻断 POST/PUT/PATCH/DELETE。
控制平面只在配置比现有进程启动时间更新时重载 Caddy/stunnel 客户端，重复运行不额外重启。

### Accounts 公共域名交接

控制平面声明可显式设置 `target.accounts_public_domain`。该值必须是目标
`domain_base` 下的合法域名，且不能与 Console/Billing 域名冲突；未声明时只服务
候选 Accounts 域名。候选模式会将显式公共别名同时加入 TLS 站点地址和
Accounts host matcher，不能只添加 matcher：CNAME 不会改写 HTTP Host/SNI。
声明别名只准备 HTTP 路由，不修改 DNS，也不转移源端数据库写入权。

验收应从真实代理检查公共域名 `/api/agent-server/v1/users` 返回可解析 JSON，
`/users/events` 返回持续事件流，以及现有代理的 `/status` 上报进入 Accounts。
仅 HTTP 200 不代表成功；空响应体必须判失败。

控制平面可显式声明 `target.billing_public_domain`，仅为公共 Billing Host
放行 `POST /v1/ingest/snapshots` 和 GET/HEAD 健康探针，Bearer 鉴权由 Billing
执行，管理接口及采集 GET 均返回 403。未声明则不增加公共入口；声明不修改 DNS。
验收必须包含缺/错 Token=401、错误方法/管理路径=403，以及真实 Vector 投递后
checkpoint、分钟桶和账本的新鲜度与正值增量。HTTP 200 空响应不能判为采集成功。

Console 公共入口由 `target.console_public_domain` 显式声明，必须是本域名基座下的普通主机名且不与 Accounts/Billing 入口冲突。角色将别名同时送入 Caddy site 与 Console host matcher，保留 Console 自己处理认证 BFF 和静态资源。仅切换 DNS、未声明对应别名不能视为入口交接完成；应在切换前用实际 Host/SNI 验证非空首页、登录 HTML 和脚本资源。
