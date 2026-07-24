# web_saas_host_config

渲染 web-saas compose 栈的**主机侧输入**:机密、配置、TLS 证书、Caddyfile。

不部署容器。compose 文件本身在
[`ai-workspace-infra/gitops`](https://github.com/ai-workspace-infra/gitops) 的
`compose/web-saas/`,由 Doco-CD 拉取。

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
- `WEB_SAAS_CONSOLE_DOMAIN`、`WEB_SAAS_ACCOUNTS_DOMAIN`

**可选**:`BILLING_DB_PASSWORD`、`OAUTH_{GITHUB,GOOGLE}_CLIENT_{ID,SECRET}`、
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
> `accounts-uat.onwalk.net`),不是 console 的域名——OAuth 回调固定落在
> accounts 服务上,console 只是发起登录跳转的一方。

## 断言在最前面,这是有意的

空口令**不会让任何东西崩溃**:postgres 带着空口令正常启动,accounts 正常监听,
部署报绿,直到有人第一次尝试登录才发现。证书缺失同理——docker 会挂载一个空
目录而不是失败。

所以这些检查必须在写任何文件之前跑,并且必须让 play 失败。
