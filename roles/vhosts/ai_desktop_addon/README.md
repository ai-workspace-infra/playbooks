# AI Desktop Addon Role (`roles/vhosts/ai_desktop_addon`)

提供面向 AI Desktop 与 CPA 节点的必备工具链与环境规范对账，作为 `roles/vhosts/icewm_desktop_minimal` 的增强依赖。

## 核心职责

1. **浏览器规范收敛**：
   - 确保 `/usr/bin/google-chrome-stable` 存在并可用。
   - 彻底清理 `/usr/local/bin/chromium` 历史禁用脚本/残余。
2. **核心运行时与开发工具**：
   - Node.js LTS (v22+)：驱动基于 npm 的各类 Agent CLI。
   - Python 3 工具链：`python3`, `python3-pip`, `python3-venv`。
3. **Agent CLI 与 CodeAgent 工具**：
   - 全局安装 `@anthropic-ai/claude-code` (`claude`)。
   - 全局安装 `@openai/codex` (`codex`)。
   - 全局安装 `@google/gemini-cli` 与 `opencode-ai`。
   - 预留 `antigravity` 与 `grok` 契约插槽。
4. **桌面交互与网络凭据工具**：
   - `xdotool`, `xclip`：桌面输入自动化与剪贴板桥接。
   - `curl`, `jq`：接口探针与元数据校验。
   - HashiCorp `vault` CLI：本地凭据读取与 CAS 对账校验。
5. **CPA 矩阵账号本地 Auth 目录安全隔离**：
   - 严格维护 4 大 CPA 账号实例本地凭据目录（`0700` 单用户权限）：
     - `/var/lib/ai-aggregator/cpa/cpa-claude-01/auth` (`ai-cpa-cpa-claude-01`)
     - `/var/lib/ai-aggregator/cpa/cpa-codex-01/auth` (`ai-cpa-cpa-codex-01`)
     - `/var/lib/ai-aggregator/cpa/cpa-antigravity-01/auth` (`ai-cpa-cpa-antigravity-01`)
     - `/var/lib/ai-aggregator/cpa/cpa-grok-01/auth` (`ai-cpa-cpa-grok-01`)
