#!/bin/bash

set -e

# ✅ 1. 安装 openssh-server
echo "🔧 安装 openssh-server..."
sudo apt update
sudo apt install -y openssh-server

# ✅ 2. 配置 sshd 默认启动（适配 systemd）
echo "📦 启用 SSH 服务..."
sudo systemctl enable ssh
sudo systemctl start ssh

# ✅ 3. 配置静态 IP（通过 systemd-networkd）
echo "🌐 配置静态 IP 地址 10.253.0.2..."
sudo mkdir -p /etc/systemd/network

cat <<EOF | sudo tee /etc/systemd/network/10-eth0-static.network
[Match]
Name=eth0

[Network]
Address=10.253.0.2/24
Gateway=10.253.0.1
DNS=8.8.8.8
EOF

sudo systemctl enable systemd-networkd
sudo systemctl restart systemd-networkd

# ✅ 4. 开启防火墙端口（可选）
# sudo ufw allow ssh

# ✅ 5. 显示信息
echo ""
echo "🎉 初始化完成！你现在可以在局域网中使用："
echo "    ssh $USER@10.253.0.2"
echo ""

