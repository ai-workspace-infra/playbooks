# AI Desktop Addon (`roles/vhosts/ai_desktop_addon`)

为 AI Desktop / CPA 节点补齐 Agent CLI、运行时与凭据目录基线。它是
[`icewm_desktop_minimal`](../icewm_desktop_minimal/README.md) 的依赖角色
（见该角色的 `meta/main.yml`），因此 **仅在使用 IceWM 桌面
（`ai_desktop_desktop_type: icewm`）时自动生效**；XFCE 桌面不会触发它，
需要时可单独 `include_role`。

> 本角色不创建 CPA 账号，也不写入任何凭据。账号 OAuth 登录由操作者在桌面或
> SSH 会话里手动完成，结果只落在各实例本地的 `auth/` 目录。

## 安装内容

| 类别 | 内容 | 说明 |
| :--- | :--- | :--- |
| 浏览器 | `/usr/bin/google-chrome-stable` | 必须已存在，缺失则断言失败；不负责安装 |
| 浏览器清理 | 删除 `/usr/local/bin/chromium` | 移除历史上的禁用包装脚本，避免被浏览器解析器误选 |
| 系统工具 | `python3`、`python3-pip`、`python3-venv`、`xdotool`、`xclip`、`curl`、`jq`、`unzip`、`git` | apt 幂等安装 |
| 运行时 | Node.js（默认 22.x） | 复用 `roles/vhosts/nodejs` |
| Agent CLI | `claude`、`codex`、`gemini`、`opencode` | `npm install -g`，包名见 `ai_desktop_addon_npm_packages` |
| Grok CLI | `grok` | xAI 官方包 `@xai-official/grok`，固定版本，见下文 |
| 凭据工具 | `vault` CLI | 从 releases.hashicorp.com 下载，缺失时才安装 |
| CPA 目录 | `/var/lib/ai-aggregator/cpa/<id>/auth` | 对账为 `0700`，属主为各实例专属用户 |

### Grok CLI

- 包：`@xai-official/grok`，发布者 `xai-security@x.ai`，可执行文件 `grok`。
- 版本固定为 `ai_desktop_addon_grok_version`（当前 `1.0.77`），保证节点间可复现。
- 升级前先核对发布者和 `dist.integrity`：

  ```bash
  npm view @xai-official/grok name version maintainers dist.integrity --json
  ```

- 设置 `ai_desktop_addon_grok_enabled: false` 可跳过安装。
- 安装后会执行 `grok --version` 作为就绪检查。

### Antigravity（`agy`）

安装 Google 官方 Antigravity CLI 1.5.3，按 CPU 架构从官方发布存储下载并校验固定 SHA-512，再安装至 `/usr/local/bin/agy`。上游入口：[官方安装文档](https://antigravity.google/docs/getting-started?tab=cli)。`ai_desktop_addon_agy_enabled: false` 可跳过；升级必须同时核验并更新版本、两个制品 URL 和摘要。系统路径由 root 持有，用户登录和自动更新不由本 role 执行；跳过安装不会卸载已有二进制。回退时重新部署先前经验证的版本或恢复先前二进制。

## CPA 账号矩阵与登录

每个 CPA 实例是一个独立账号，监听本机回环端口，凭据目录互相隔离：

| 实例 | 属主 | CLI | 目录 |
| :--- | :--- | :--- | :--- |
| `cpa-claude-01` | `ai-cpa-cpa-claude-01` | `claude` | `/var/lib/ai-aggregator/cpa/cpa-claude-01/auth` |
| `cpa-codex-01` | `ai-cpa-cpa-codex-01` | `codex` | `/var/lib/ai-aggregator/cpa/cpa-codex-01/auth` |
| `cpa-antigravity-01` | `ai-cpa-cpa-antigravity-01` | `antigravity` | `/var/lib/ai-aggregator/cpa/cpa-antigravity-01/auth` |
| `cpa-grok-01` | `ai-cpa-cpa-grok-01` | `grok` | `/var/lib/ai-aggregator/cpa/cpa-grok-01/auth` |

登录流程：

1. 通过 AI Desktop 桌面或 SSH 进入节点。
2. 运行对应 CLI 的登录命令（如 `claude login`、`codex login`；`grok` 首次启动会唤起浏览器授权）。
3. 在桌面内的 Chrome 完成授权，回调后的凭据位置及格式需按 CPA 的登录流程核验，不能假定普通 CLI 登录自动写入 CPA 目录。
4. 服务器或 CI 场景可改用 `XAI_API_KEY` 等环境变量，但必须由 Vault 在运行时注入，不得写入 Git、inventory 或 systemd unit。

## 变量

| 变量 | 默认值 | 作用 |
| :--- | :--- | :--- |
| `ai_desktop_addon_enabled` | `true` | 总开关 |
| `ai_desktop_addon_enforce_chrome` | `true` | 校验 Chrome 存在 |
| `ai_desktop_addon_remove_chromium_wrapper` | `true` | 删除 chromium 包装脚本 |
| `ai_desktop_addon_nodejs_enabled` | `true` | 是否安装 Node.js |
| `ai_desktop_addon_nodejs_major_version` | `22` | Node.js 主版本 |
| `ai_desktop_addon_npm_packages` | claude-code、codex、gemini-cli、opencode-ai | 全局 npm 包 |
| `ai_desktop_addon_agy_enabled` | `true` | 是否安装 Antigravity CLI |
| `ai_desktop_addon_agy_version` | `1.5.3` | 固定制品版本 |
| `ai_desktop_addon_agy_artifacts` | Linux amd64/arm64 | 固定 URL 和 SHA-512 |
| `ai_desktop_addon_grok_enabled` | `true` | 是否安装 Grok CLI |
| `ai_desktop_addon_grok_npm_package` | `@xai-official/grok` | Grok CLI 包名 |
| `ai_desktop_addon_grok_version` | `1.0.77` | Grok CLI 固定版本 |
| `ai_desktop_addon_install_vault_cli` | `true` | 是否安装 vault CLI |
| `ai_desktop_addon_vault_version` | `1.18.2` | vault CLI 版本 |
| `ai_desktop_addon_cpa_root` | `/var/lib/ai-aggregator/cpa` | CPA 根目录 |
| `ai_desktop_addon_cpa_matrix` | 4 个实例 | 实例、属主与 CLI 对应关系 |

## 约束

- 仅支持 Debian 系主机，其他系统会断言失败。
- 不在 Git、日志或 systemd unit 中保存任何 token 或 OAuth bundle。
- 一个节点对应一组账号（`ai_desktop_account_scope: single`）；同一个 Provider
  账号不要在两个节点同时登录。

## 使用

```bash
ansible-playbook -i <inventory> -l <host> deploy_ai_desktop.yml \
  -e ai_desktop_desktop_type=icewm
```

验证：

```bash
for c in node npm claude codex gemini opencode grok vault xdotool xclip jq; do
  printf '%-10s' "$c"; command -v "$c" || echo MISSING
done
ls -ld /var/lib/ai-aggregator/cpa/*/auth
```
