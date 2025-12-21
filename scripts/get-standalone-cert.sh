#!/bin/bash

set -e

DOMAIN="sing-box.onwalk.net"
SSL_KEY="/etc/ssl/${DOMAIN}.key"
SSL_PEM="/etc/ssl/${DOMAIN}.pem"

# 1. 安装 acme.sh（如果未安装）
if [ ! -d "$HOME/.acme.sh" ]; then
  echo "Installing acme.sh..."
  curl https://get.acme.sh | sh
  export PATH="$HOME/.acme.sh:$PATH"
else
  echo "acme.sh already installed."
fi

# 2. 申请 RSA 证书（使用 HTTP-01 验证，需 80 端口可用）
echo "Issuing certificate for $DOMAIN using standalone mode..."
~/.acme.sh/acme.sh --issue --standalone -d "$DOMAIN" --keylength 2048

# 3. 安装证书到指定位置
echo "Installing cert to $SSL_PEM and $SSL_KEY..."
~/.acme.sh/acme.sh --install-cert -d "$DOMAIN" \
  --key-file "$SSL_KEY" \
  --fullchain-file "$SSL_PEM" \
  --reloadcmd "systemctl restart sing-box"

# 4. 设置权限
chmod 600 "$SSL_KEY"
chmod 644 "$SSL_PEM"
echo "Certificate successfully installed."

# 5. 提示
echo "Done. Cert saved at:"
echo "  Key:  $SSL_KEY"
echo "  Cert: $SSL_PEM"

