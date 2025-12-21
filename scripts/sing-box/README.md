
# Sing-box VLESS + Reality 一键部署脚本

该脚本用于在 Linux 服务器上快速部署一个基于 sing-box 的隐匿代理服务，采用 `VLESS + Reality` 协议，结合 systemd 自启动支持，适用于高隐蔽性代理通信场景。

---

## 🧩 功能特性

- 🚀 自动安装并配置 sing-box（如未安装）
- 🔐 自动生成 Reality 密钥对（无需手动管理）
- 📄 自动生成服务端配置文件（支持伪装 SNI）
- ⚙️ 自动创建并启用 systemd 启动服务
- 📦 自动输出客户端配置片段，支持 Windows/macOS/Linux

---

## 🖥️ 支持平台

- 服务端：Debian / Ubuntu / CentOS / Arch / 兼容 Linux 系统
- 客户端平台：macOS / Windows / Linux（任意 sing-box 客户端）

---

## ⚙️ 使用方式

### 一键安装（推荐）

```bash
bash <(curl -fsSL https://your.cdn/installer/install-singbox.sh) \
  --ip 123.123.123.123 \
  --sni www.bing.com \
  --client-platform macos

  参数说明：

参数	示例值	说明
--ip	123.123.123.123	当前服务器公网 IP
--sni	www.bing.com	Reality 伪装域名
--client-platform	macos / windows / linux	客户端类型（影响输出说明）

📂 脚本行为说明
部署完成后，脚本会生成：

文件路径	说明
/etc/sing-box/config-server.json	sing-box 服务端配置
/etc/systemd/system/sing-box.service	systemd 启动配置
/usr/local/bin/sing-box	主程序（如未安装将自动下载）

并自动执行：

bash
复制
编辑
systemctl daemon-reload
systemctl enable --now sing-box
🔐 示例输出
部署成功后会输出如下：

css
复制
编辑
✅ 服务端已部署成功！
👉 Reality 公钥: yyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyy
👉 ShortID: abcd
👉 UUID: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx

📦 推荐客户端配置如下：
{
  "outbounds": [
    {
      "type": "vless",
      ...
    }
  ]
}
🧱 安全建议
建议使用 Cloudflare DNS 或境外解析加快 SNI 匹配

Reality 不需要 TLS 证书即可启用加密通信

可进一步结合 iptables 或 fail2ban 做入站控制

🛠️ 后续扩展（可选）
你可以基于本项目扩展支持：

fallback 到 nginx / 80 端口

多用户（多个 UUID）

动态配置（通过 API 控制）

客户端同步配置工具


