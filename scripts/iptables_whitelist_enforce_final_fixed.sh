#!/bin/bash
# 只使用 iptables 管理白名单控制脚本

# 初始化配置
ALLOW_ALL_IPS=(
  127.0.0.1
  188.104.180.76 188.104.188.100 188.104.208.200
  188.104.198.244 188.104.138.144 188.105.244.69
  188.104.229.244 188.104.219.244 188.104.158.196
  188.104.174.47 188.104.150.147
  188.104.180.88 188.104.180.89 188.104.151.7 188.104.151.8
  188.105.215.5 188.105.215.6 188.104.220.8 188.104.220.9
  188.104.159.5 188.104.159.6 188.104.190.16 188.104.190.17
  188.104.230.5 188.104.230.6 188.104.173.5 188.104.173.6
  188.104.199.144 188.104.199.145 188.104.209.49 188.104.209.52
  188.104.140.5 188.104.140.6
  10.212.222.22 10.212.222.34
  188.104.77.15 188.104.77.19
  10.76.142.186 10.76.142.187
  10.76.149.128
)
ALLOW_CIDRS=(
  10.76.144.0/25
  188.104.29.0/24
)

ACTION="$1"

if [[ -z "$ACTION" ]]; then
  echo "用法: $0 {add|delete|show}"
  exit 1
fi

echo ">>> 模式: $ACTION"
echo ">>> 所有非白名单来源将被拒绝"
echo ""

is_ipv6() {
  [[ "$1" == *:* ]]
}

run_cmd() {
  local cmd="$1"
  echo "[RUN] $cmd"
  eval "$cmd"
}

# 生成 iptables 规则
generate_iptables_rules() {

  # 放行 ICMP 和 ICMPv6 规则（优先级最高）
  echo "iptables -I INPUT -p icmp -j ACCEPT"
  echo "ip6tables -I INPUT -p ipv6-icmp -j ACCEPT"

  # 生成允许的 IP 规则
  for ip in "${ALLOW_ALL_IPS[@]}"; do
    echo "iptables -I INPUT -s $ip -j ACCEPT"
  done

  # 生成允许的 CIDR 规则
  for cidr in "${ALLOW_CIDRS[@]}"; do
    echo "iptables -I INPUT -s $cidr -j ACCEPT"
  done

  # 默认 DROP 规则
  echo "iptables -A INPUT -j DROP"
}

# 删除指定 iptables 规则
delete_iptables_rules() {

  # 删除放行 ICMP 和 ICMPv6 规则（优先级最高）
  echo "iptables -D INPUT -p icmp -j ACCEPT"
  echo "ip6tables -D INPUT -p ipv6-icmp -j ACCEPT"

  # 删除允许的 IP 规则
  for ip in "${ALLOW_ALL_IPS[@]}"; do
    echo "iptables -D INPUT -s $ip -j ACCEPT"
  done

  # 删除允许的 CIDR 规则
  for cidr in "${ALLOW_CIDRS[@]}"; do
    echo "iptables -D INPUT -s $cidr -j ACCEPT"
  done

  # 删除默认 DROP 规则
  echo "iptables -D INPUT -j DROP"
}

# 查看当前规则
show_iptables_rules() {
  echo "============= iptables -S ============="
  iptables -S INPUT | sed 's/^-A /iptables -C /'
  echo "============= ip6tables -S ============="
  ip6tables -S INPUT | sed 's/^-A /ip6tables -C /'
}

# 执行操作
case "$ACTION" in
  add)
    generate_iptables_rules > iptables_rules.sh
    echo "[INFO] 规则已生成并保存为 iptables_rules.sh 文件"
    bash iptables_rules.sh
    ;;
  delete)
    delete_iptables_rules > delete_iptables_rules.sh
    echo "[INFO] 删除规则已保存为 delete_iptables_rules.sh 文件"
    bash delete_iptables_rules.sh
    ;;
  show)
    show_iptables_rules
    ;;
  *)
    echo "无效的操作: $ACTION"
    exit 1
    ;;
esac

echo ">>> 操作完成。"

