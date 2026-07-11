# accounts OAuth 部署接线

> **Status**: ✅ 已合并上线(GitHub OAuth 线上验证 307 通过)
> **Date**: 2026-07-11
> **Related PRs**: [#111](https://github.com/ai-workspace-infra/playbooks/pull/111) [#112](https://github.com/ai-workspace-infra/playbooks/pull/112)
> **上游联动**: ai-workspace-services/accounts #12 #13 #14 #16(见该仓 `docs/tasks/2026-07-11-oauth-login.md`)
> **Role**: `roles/vhosts/accounts_service` · **Playbook**: `deploy_accounts_svc_plus.yml`

## 目标

accounts.svc.plus 走本仓部署链(GitHub Actions → 本仓 `deploy_accounts_svc_plus.yml` → VPS docker compose),需把 OAuth/JWT 密钥注入容器 env,并修复部署过程暴露的既有 bug。

## PR 明细

### #111 [MERGED] — Wire GitHub/Google OAuth + auth token env into role
- `templates/app.env.j2`:增 `AUTH_TOKEN_*`、`GITHUB_CLIENT_*`、`GOOGLE_CLIENT_*`
- `defaults/main.yml`:`accounts_service_env_defaults` 增对应 `lookup('ansible.builtin.env', 'OAUTH_GITHUB_*'/'OAUTH_GOOGLE_*'/'AUTH_TOKEN_*')`。CI 侧用 `OAUTH_GITHUB_*`/`OAUTH_GOOGLE_*` 前缀(**Actions 禁 secret/var 以 `GITHUB_` 开头**),role 映射回容器内 `GITHUB_CLIENT_*`/`GOOGLE_CLIENT_*`
- `tasks/target.yml`:新增 lineinfile 任务(带 `no_log`),保证**存量主机**已有的 `app.env` 也被更新(模板任务只在文件不存在时渲染)
- `templates/account.yaml.j2`:去掉 envsubst 不支持的 `${VAR:-default}` 语法(该写法导致线上 JWT 密钥曾渲染为字面量垃圾串);`frontendUrl` 改 Jinja 直渲;`redirectUrl` 拆 per-provider

### #112 [MERGED] — Fix accounts deploy: guard ansible_os_family (gather_facts:false)
- `deploy_accounts_svc_plus.yml` 用 `gather_facts: false`,而 `defaults/main.yml` 的 `accounts_service_caddy_base_dir` 直接解引用 `ansible_os_family`(macOS all-in-one Darwin 分支遗留)→ 部署过了 SSH prep 就炸 `'ansible_os_family' is undefined`
- 修复:`(ansible_os_family | default('')) == 'Darwin'`,未采集事实时回落 Linux caddy 根;Darwin 分支在采集事实时(all-in-one)仍正常
- 之前所有 accounts 部署都死在 "Prepare Runner SSH Access"(缺 SSH secret),从没跑到这个 task,所以该 bug 一直没暴露

## 关键机制备查

- **envsubst 不支持 `${VAR:-default}`**:模板由容器 entrypoint 用 envsubst 渲染,只认纯 `${VAR}`;默认值写法会原样留下成垃圾串。本仓所有 accounts 模板已清理,但其它 role 若有同款写法需排查。
- **app.env 模板只在文件缺失时渲染**:存量主机改 env 必须走 `tasks/target.yml` 的 lineinfile,光改模板无效。
- **部署目标**:`accounts_service_hosts` → jp-xhttp-contabo.svc.plus(= install.svc.plus,46.250.251.132)。

## 遗留待办(部署侧)

- [ ] **immutable 锁**:`/opt/cloud-neutral/accounts/managed/prod` 曾被手动 `chattr +i` 整树锁死,role 不管理该标志;本次靠手动 `chattr -R -i` 解锁。若要保留硬化,应在 role 加"部署后置上锁"步骤,否则每次部署都会因不可写而失败
- [ ] **caddy 片段命名**:role 写 `conf.d/accounts.caddy`,与 host 约定 `<domain>.svc.plus.caddy` 不一致,曾与旧文件并存导致 `ambiguous site definition`。建议 role 改写 `accounts.svc.plus.caddy` 对齐(旧文件已 mv 备份)
- [ ] `deploy_accounts_svc_plus.yml` 仍 checkout `x-evor/playbooks`(重定向到本仓),可改为规范名
