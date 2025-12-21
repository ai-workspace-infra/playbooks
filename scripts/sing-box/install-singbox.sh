#!/bin/bash

set -e

# 参数
SERVER_IP="$1"
SNI="$2"
CLIENT_PLATFORM="$3"

# 示例用法提示
if [[ -z "$SERVER_IP" || -z "$SNI" || -z "$CLIENT_PLATFORM" ]]; then
  echo "用法: $0 --ip <服务器IP> --sni <伪装域名> --client-platform <macos|linux|windows>"
  exit 1
fi

UUID=$(uuidgen)
KEYPAIR=$(sing-box generate reality-keypair)
PRIVATE_KEY=$(echo "$KEYPAIR" | grep PrivateKey | awk '{print $2}')
PUBLIC_KEY=$(echo "$KEYPAIR" | grep PublicKey | awk '{print $2}')
SHORT_ID=$(head /dev/urandom | tr -dc a-z0-9 | head -c 4)

# 安装 sing-box（以 Debian 为例）
if ! command -v sing-box &>/dev/null; then
  echo "🔧 安装 sing-box..."
  curl -fsSL https://sing-box.app/install | bash
fi

# 创建配置目录
mkdir -p /etc/sing-box

# 写入服务端配置
cat > /etc/sing-box/config-server.json <<EOF
{
  "log": {
    "level": "info"
  },
  "inbounds": [
    {
      "type": "vless",
      "tag": "vless-in",
      "listen": "::",
      "listen_port": 443,
      "users": [
        {
          "uuid": "$UUID",
          "flow": ""
        }
      ],
      "tls": {
        "enabled": true,
        "server_name": "$SNI",
        "reality": {
          "enabled": true,
          "handshake": {
            "server": "$SNI",
            "server_port": 443
          },
          "private_key": "$PRIVATE_KEY",
          "short_id": ["$SHORT_ID"]
        }
      }
    }
  ],
  "outbounds": [
    {
      "type": "direct",
      "tag": "direct"
    }
  ]
}
EOF

# 写入 systemd 文件
cat > /etc/systemd/system/sing-box.service <<EOF
[Unit]
Description=Sing-box Proxy Service
After=network.target

[Service]
ExecStart=/usr/local/bin/sing-box run -c /etc/sing-box/config-server.json
Restart=on-failure
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
EOF

# 启动服务
systemctl daemon-reexec
systemctl daemon-reload
systemctl enable sing-box --now

# 客户端配置片段
echo ""
echo "✅ 服务端已部署成功！"
echo "👉 Reality 公钥: $PUBLIC_KEY"
echo "👉 ShortID: $SHORT_ID"
echo "👉 UUID: $UUID"
echo ""
echo "📦 推荐客户端配置如下："

cat <<EOF

{
  "outbounds": [
    {
      "type": "vless",
      "tag": "proxy-out",
      "server": "$SERVER_IP",
      "server_port": 443,
      "uuid": "$UUID",
      "flow": "",
      "tls": {
        "enabled": true,
        "server_name": "$SNI",
        "utls": {
          "enabled": true,
          "fingerprint": "chrome"
        },
        "reality": {
          "enabled": true,
          "public_key": "$PUBLIC_KEY",
          "short_id": "$SHORT_ID"
        }
      }
    }
  ]
}
EOF

# 可选：根据客户端平台提醒适配位置
if [[ "$CLIENT_PLATFORM" == "macos" || "$CLIENT_PLATFORM" == "linux" ]]; then
  echo -e "\n📂 请将此配置合并到你的 sing-box 客户端配置文件中，如 ~/.config/sing-box/config.json"
elif [[ "$CLIENT_PLATFORM" == "windows" ]]; then
  echo -e "\n📂 请将此配置合并到你的 Windows sing-box GUI 或 config.json 文件中"
fi
