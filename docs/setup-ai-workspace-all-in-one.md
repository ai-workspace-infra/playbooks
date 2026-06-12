# AI Workspace 一键部署与全局安全网络配置向导

`setup-ai-workspace-all-in-one.yml` 是用于在目标 VPS 上完整、自动化地拉起 AI 研发环境底层组件与服务的聚合 Playbook。

> [!TIP]
> ## ⏳ TL;DR (太长不看版)
> 
> **一键标准部署 (无需本地 Ansible 环境，直接在目标机执行)：**
> ```bash
> curl -sfL https://raw.githubusercontent.com/ai-workspace-infra/playbooks/main/setup-ai-workspace-all-in-one.sh | VAULT_PASS="您的密码" bash -
> ```
> 
> **一键极严防御部署 (瘫痪所有外网接口，强制全内网/VPN架构)：**
> ```bash
> curl -sfL https://raw.githubusercontent.com/ai-workspace-infra/playbooks/main/setup-ai-workspace-all-in-one.sh | AI_WORKSPACE_SECURITY_LEVEL=strict VAULT_PASS="您的密码" bash -
> ```
> 
> **组合技：极严防御 + 单独开白名单口子 (如仅开放 LiteLLM 接口)：**
> ```bash
> curl -sfL https://raw.githubusercontent.com/ai-workspace-infra/playbooks/main/setup-ai-workspace-all-in-one.sh | AI_WORKSPACE_SECURITY_LEVEL=strict LITELLM_API_CADDY_STRICT_WHITELIST=true VAULT_PASS="您的密码" bash -
> ```
> 
> **高级定制：一键部署全架构并按需开启可选功能 (如 XRDP)：**
> ```bash
> curl -sfL https://raw.githubusercontent.com/ai-workspace-infra/playbooks/main/setup-ai-workspace-all-in-one.sh | \
>   XWORKSPACE_CONSOLE_ENABLE_XRDP=true \
>   XWORKSPACE_CONSOLE_PUBLIC_ACCESS=true \
>   XWORKMATE_BRIDGE_PUBLIC_ACCESS=true \
>   GATEWAY_OPENCLAW_PUBLIC_ACCESS=false \
>   VAULT_PUBLIC_ACCESS=false \
>   LITELLM_API_CADDY_STRICT_WHITELIST=true \
>   VAULT_PASS="您的密码" \
>   bash -
> ```

本文档将详细介绍它的基础用法，并重点讲解如何通过内置的全局开关与细粒度 `public_access` 控制，打造出“最严安全网络架构”（断开一切外部 Web 端口代理，仅限加密 VPN 内网互联）。

---

## 1. 常规快速部署

如果您希望采用**标准（Standard）安全模式**部署（即：允许需要对外提供部分 Web/API 接口的应用如 `XWorkmate Bridge` 通过 HTTPS 暴露到公网，但内部组件互相隔离）。

```bash
ansible-playbook -i inventory.ini setup-ai-workspace-all-in-one.yml \
  --limit jp-xhttp-contabo.svc.plus \
  --vault-password-file ~/.vault_password
```

---

## 2. 极致安全：强制全隔离模式 (VPN Only)

如果您正在处理高敏感度的业务，或目标服务器被作为纯后台的 AI 基础设施节点。您可以选择将其配置为**最严的安全等级 (Strict)**。

在此模式下，任何默认开放外网的应用，都将被**强制剥夺公网入口（其 Caddy 代理配置或 K8s Ingress 将被直接销毁删除）。外部黑客或扫描器即便知道子域名，也无法解析请求到您的端口，此时访问服务器上的任何 AI 服务，全部必须经过内部加密隧道（例如 WireGuard / Tailscale 等 VPN 虚拟局域网）。**

**执行部署命令：**
```bash
ansible-playbook -i inventory.ini setup-ai-workspace-all-in-one.yml \
  --limit jp-xhttp-contabo.svc.plus \
  --vault-password-file ~/.vault_password \
  -e "ai_workspace_security_level=strict"
```

---

## 3. 个性化服务放行与阻断 (-e 开关详解)

系统设计了精细化的权限参数，可以在 `standard` 安全模式的基础下，针对某个独立应用进行公网切断；又或者在 `strict` 极致安全模式的底座上，单独给某个应用“开一个白名单口子”。

### 全局策略控制开关
- `-e "ai_workspace_security_level=strict"`
  * **作用：** 一键切断所有默认带有对外出口的组件。覆盖掉下述开关的默认策略，将其全部强转为 `false`。

### 细粒度服务暴露开关 (支持针对性覆盖)

1. **XWorkspace Console (底层主工作区门户) 公网访问控制**
   - **默认值：** `true` (standard 下) / `false` (strict 下)
   - **参数：** `-e "xworkspace_console_public_access=false"`
   - **作用：** 设为 true 时，会自动将本地 17000 端口通过 Caddy 反向代理到绑定的 `workspace.svc.plus` 域名提供公网访问。设为 false 时则销毁对应代理文件，只能进服务器内网/XRDP访问。

2. **XWorkmate Bridge 公网访问控制**
   - **默认值：** `true` (standard 下) / `false` (strict 下)
   - **参数：** `-e "xworkmate_bridge_public_access=false"`
   - **作用：** 设为 false 时，会彻底删除该服务在 Caddy `/etc/caddy/conf.d` 中的 `.caddy` 文件，使其失去从外界 HTTPS 进入内部 8787 端口的路径。

3. **OpenClaw Gateway 公网访问控制**
   - **默认值：** `false` (无论在何种策略下，底层模型网关默认不允许直接向公网打开界面入口)
   - **参数：** `-e "gateway_openclaw_public_access=true"`
   - **作用：** 当您在出差时，身边没有 VPN 环境，但迫切需要连接远程 OpenClaw 平台时，可以通过将其设为 true 临时生成 Caddy 文件，恢复它的公网域名入口访问。

4. **Vault KMS 密钥中心公网访问控制**
   - **默认值：** `false`
   - **参数：** `-e "vault_public_access=true"`
   - **作用：** 设为 false 时，该服务在 K8s 中部署的 Helm `ingress.enabled` 配置会被强制渲染为 false，不会向集群外网注册路由。设为 true 时方可绑定公网 Ingress Class 域名。

5. **LiteLLM 轻量网关访问行为控制**
   - **默认值：** `false`
   - **参数：** `-e "litellm_api_caddy_strict_whitelist=true"` 
   - **作用：** 这个参数用于对 Caddy 代理行为做进一步保护，开启后，Caddy 会拦截一切没有命中官方兼容模型路径（如 `/v1/chat/completions`）的请求并拦截响应为 `404`，例如阻断前端 Dashboard UI（`/ui*`）的外网暴露。

6. **按需开启 XRDP 远程桌面连接**
   - **默认值：** `false`
   - **参数：** `-e "xworkspace_console_enable_xrdp=true"`
   - **作用：** XFCE 桌面环境默认仅提供基于 Web 浏览器的 Console UI，如需通过原生 RDP 客户端（如 Windows 远程桌面）连接目标主机，可增加此参数。

## 典型组合使用场景

**场景：开启 Strict 全局断网防护，但唯独开放 LiteLLM 模型 API 入口供第三方业务端点调用，且通过最严格白名单防护。**

```bash
ansible-playbook -i inventory.ini setup-ai-workspace-all-in-one.yml \
  --limit jp-xhttp-contabo.svc.plus \
  --vault-password-file ~/.vault_password \
  -e "ai_workspace_security_level=strict" \
  -e "litellm_api_caddy_strict_whitelist=true"
```
这种精细的声明式管理，能确保基础设施按照 Infrastructure as Code (IaC) 的最佳安全实践被可预测地配置。
