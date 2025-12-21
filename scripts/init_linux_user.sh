#!/bin/bash

# 检查是否传入了用户名和密码参数
if [ -z "$1" ] || [ -z "$2" ]; then
  echo "Usage: $0 <username> <password>"
  exit 1
fi

# 定义变量
USERNAME="$1"  # 使用传入的第一个参数作为用户名
PASSWORD="$2"  # 使用传入的第二个参数作为密码
SSH_KEY_PATH="/root/.ssh/authorized_keys"  # 替换为实际公钥文件路径
HOME_DIR="/home/$USERNAME"
SSH_DIR="$HOME_DIR/.ssh"
AUTHORIZED_KEYS="$SSH_DIR/authorized_keys"

# 创建用户并设置家目录
sudo useradd -m -s /bin/bash -G sudo $USERNAME

# 设置用户密码
echo "$USERNAME:$PASSWORD" | sudo chpasswd

# 创建 .ssh 目录
sudo mkdir -p $SSH_DIR

# 设置目录权限
sudo chmod 700 $SSH_DIR
sudo chown $USERNAME:$USERNAME $SSH_DIR

# 将公钥内容写入 authorized_keys 文件
sudo bash -c "cat $SSH_KEY_PATH > $AUTHORIZED_KEYS"

# 设置 authorized_keys 文件权限
sudo chmod 600 $AUTHORIZED_KEYS
sudo chown $USERNAME:$USERNAME $AUTHORIZED_KEYS

# 确保用户可以使用 sudo 不需要输入密码
echo "$USERNAME ALL=(ALL) NOPASSWD:ALL" | sudo tee /etc/sudoers.d/$USERNAME

echo "User $USERNAME has been created, password set, and configured with sudo privileges successfully."
