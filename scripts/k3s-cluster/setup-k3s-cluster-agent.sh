#!/bin/bash
set -e

# ============================================================
# 🧩 setup-k3s-agent.sh
# Version: v1.0.0
# Last Updated: 2025-03-14
# Description: 一键安装 k3s agent 节点，支持国内/国际网络智能识别
# ============================================================

print_usage() {
  echo "Usage:"
  echo "  $0 <SERVER_NODE_IP> <K3S_TOKEN>"
  exit 1
}

is_in_china() {
  local cn_score=0
  local global_score=0

  echo "🌐 检测网络环境中..."

  ping -c 1 -W 1 www.baidu.com &>/dev/null && ((cn_score++))
  ping -c 1 -W 1 www.aliyun.com &>/dev/null && ((cn_score++))
  ping -c 1 -W 1 www.163.com &>/dev/null && ((cn_score++))

  ping -c 1 -W 1 www.cloudflare.com &>/dev/null && ((global_score++))
  ping -c 1 -W 1 www.wikipedia.org &>/dev/null && ((global_score++))
  ping -c 1 -W 1 www.google.com &>/dev/null && ((global_score++))

  echo "📶 Ping 评分: CN=$cn_score, GLOBAL=$global_score"

  if [[ $cn_score -ge $global_score ]]; then
    return 0
  else
    return 1
  fi
}

install_k3s_agent() {
  local SERVER_NODE_IP=$1
  local K3S_TOKEN=$2

  [[ -z "$SERVER_NODE_IP" || -z "$K3S_TOKEN" ]] && print_usage

  local NODE_IP
  NODE_IP=$(hostname -I | awk '{print $1}')

  local INSTALL_K3S_EXEC="agent --server=https://${SERVER_NODE_IP}:6443 --node-ip=${NODE_IP} --token=${K3S_TOKEN}"

  echo "🔧 Agent 节点参数:"
  echo "  SERVER_NODE_IP=${SERVER_NODE_IP}"
  echo "  NODE_IP=${NODE_IP}"
  echo "  K3S_TOKEN=<hidden>"

  if is_in_china; then
    echo "🌏 检测到中国大陆网络，使用国内加速源"
    export INSTALL_K3S_MIRROR=cn
    INSTALL_K3S_URL="https://rancher-mirror.rancher.cn/k3s/k3s-install.sh"
  else
    echo "🌍 检测到国际网络，使用默认安装源"
    INSTALL_K3S_URL="https://get.k3s.io"
  fi

  curl -sfL "$INSTALL_K3S_URL" -o install_k3s.sh && chmod +x install_k3s.sh
  INSTALL_K3S_EXEC="$INSTALL_K3S_EXEC" ./install_k3s.sh

  echo "✅ K3s Agent 安装完成"
}

# === 主流程入口 ===
install_k3s_agent "$1" "$2"
