#!/bin/bash
# FIX config vtap-group-id-request 20250612-15:10

set -e

####################################
# 🌐 配置区
####################################

IP_LIST="./ip.list"
SERVICE_NAME="deepflow-agent"
PKG_DIR="deepflow-agent-for-linux"
MAX_PARALLEL=5

CONTROLLER_IP=""
VTAP_GROUP_ID=""
LIMIT=""

SSH_OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=15"

FAILED_FILE="failed_hosts.txt"
SUCCESS_FILE="success_hosts.txt"
> "$FAILED_FILE"
> "$SUCCESS_FILE"

####################################
# 参数解析
####################################

if [[ $# -eq 0 ]]; then
  echo "用法: $0 {deploy|upgrade|verify} --controller <ip> --group <id> [--limit ip1,ip2]"
  exit 1
fi

ACTION="$1"
shift

while [[ $# -gt 0 ]]; do
  case "$1" in
    --controller)
      CONTROLLER_IP="$2"
      shift 2
      ;;
    --group)
      VTAP_GROUP_ID="$2"
      shift 2
      ;;
    --limit)
      LIMIT="$2"
      shift 2
      ;;
    *)
      echo "未知参数: $1"
      exit 1
      ;;
  esac
done

if [[ "$ACTION" != "deploy" && "$ACTION" != "upgrade" && "$ACTION" != "verify" ]]; then
  echo "用法: $0 {deploy|upgrade|verify} --controller <ip> --group <id> [--limit ip1,ip2]"
  exit 1
fi

if [[ "$ACTION" != "verify" && ( -z "$CONTROLLER_IP" || -z "$VTAP_GROUP_ID" ) ]]; then
  echo "❗ deploy/upgrade 必须传入 --controller 和 --group 参数"
  exit 1
fi

####################################
# 核心函数
####################################

worker() {
  local ip="$1"
  local user="$2"
  local pass="$3"

  echo "🔧 [$ACTION] 处理主机 $ip ($user)"

  if [[ "$ACTION" == "verify" ]]; then
    verify_agent "$ip" "$user" "$pass" && {
      echo "$ip" >> "$SUCCESS_FILE"
      return
    } || {
      echo "$ip" >> "$FAILED_FILE"
      return
    }
  fi

  remote_info=$(fetch_remote_info "$ip" "$user" "$pass") || {
    echo "❌ $ip 获取远程信息失败"
    echo "$ip" >> "$FAILED_FILE"
    return
  }

  arch=$(echo "$remote_info" | cut -d'|' -f1)
  init=$(echo "$remote_info" | cut -d'|' -f2)

  if [[ "$init" == "unknown" ]]; then
    echo "❌ $ip 不支持的初始化系统: $init"
    echo "$ip" >> "$FAILED_FILE"
    return
  fi

  pkg_path=$(choose_agent_package "$arch" "$init")

  if [[ "$pkg_path" == "UNSUPPORTED" ]]; then
    echo "❌ $ip 无匹配安装包: $arch/$init"
    echo "$ip" >> "$FAILED_FILE"
    return
  fi

  install_agent "$ip" "$user" "$pass" "$pkg_path" && update_config "$ip" "$user" "$pass" && {
    echo "✅ $ip $ACTION 完成"
    echo "$ip" >> "$SUCCESS_FILE"
  } || {
    echo "❌ $ip 安装或配置失败"
    echo "$ip" >> "$FAILED_FILE"
  }

  echo "-------------------------------------------"
}

fetch_remote_info() {
  local ip="$1" user="$2" pass="$3"

  sshpass -p "$pass" ssh $SSH_OPTS "$user@$ip" bash <<'EOF'
arch=$(uname -m)
case "$arch" in
  aarch64|arm64) arch="arm" ;;
  *) arch="x86" ;;
esac

if command -v systemctl >/dev/null; then init=systemd;
elif command -v initctl >/dev/null; then init=upstart;
else init=unknown; fi

echo "${arch}|${init}"
EOF
}

choose_agent_package() {
  local arch="$1" init="$2"

  shopt -s nullglob

  declare -a patterns

  if [[ "$arch" == "arm" ]]; then
    patterns=("$PKG_DIR"/deepflow-agent-*.$init.aarch64.*)
  else
    patterns=("$PKG_DIR"/deepflow-agent-*.$init-x86.* \
              "$PKG_DIR"/deepflow-agent-*.$init.*)
  fi

  files=()

  for pattern in "${patterns[@]}"; do
    for file in $pattern; do
      files+=("$file")
    done
  done

  if [[ ${#files[@]} -gt 0 ]]; then
    latest=$(printf "%s\n" "${files[@]}" | sort -V | tail -1)
    echo "🎯 选择安装包: $latest" >&2
    echo "$latest"
  else
    echo "UNSUPPORTED"
  fi
}

install_agent() {
  local ip="$1" user="$2" pass="$3" pkg_path="$4"
  local remote_pkg="/tmp/agent.${pkg_path##*.}"

  sshpass -p "$pass" scp $SSH_OPTS "$pkg_path" "$user@$ip:$remote_pkg"

  sshpass -p "$pass" ssh $SSH_OPTS "$user@$ip" bash <<EOF
set -e
if command -v sudo >/dev/null; then SUDO="sudo"; else SUDO=""; fi

if [[ "$remote_pkg" == *.rpm ]]; then
  \$SUDO rpm -Uvh --replacepkgs "$remote_pkg"
elif [[ "$remote_pkg" == *.deb ]]; then
  \$SUDO dpkg -i "$remote_pkg" || \$SUDO apt-get install -f -y
else
  echo "❌ 不支持的安装包格式"
  exit 1
fi

if command -v systemctl &>/dev/null; then
  \$SUDO systemctl enable $SERVICE_NAME
  \$SUDO systemctl restart $SERVICE_NAME
elif command -v service &>/dev/null; then
  \$SUDO service $SERVICE_NAME restart
  \$SUDO chkconfig $SERVICE_NAME on
elif command -v initctl &>/dev/null; then
  \$SUDO initctl restart $SERVICE_NAME || \$SUDO initctl start $SERVICE_NAME
else
  echo "❌ 无法识别服务管理方式"
fi
EOF
}

update_config() {
  local ip="$1" user="$2" pass="$3"
  sshpass -p "$pass" ssh $SSH_OPTS "$user@$ip" bash <<EOF
set -e
if command -v sudo >/dev/null; then SUDO="sudo"; else SUDO=""; fi
CONFIG_FILE="/etc/deepflow-agent.yaml"
\$SUDO mkdir -p \$(dirname \$CONFIG_FILE)
cat <<CFG | \$SUDO tee "\$CONFIG_FILE" >/dev/null
controller-ips:
  - $CONTROLLER_IP
vtap-group-id-request: "$VTAP_GROUP_ID"
CFG
\$SUDO chmod 644 "\$CONFIG_FILE"
\$SUDO chown root:root "\$CONFIG_FILE"
EOF
}

verify_agent() {
  local ip="$1" user="$2" pass="$3"
  echo "🔍 $ip 状态检查："
  sshpass -p "$pass" ssh $SSH_OPTS "$user@$ip" "
    systemctl is-active $SERVICE_NAME 2>/dev/null || \
    service $SERVICE_NAME status || \
    initctl status $SERVICE_NAME
  "
}

####################################
# 并发控制主逻辑
####################################

sem(){
  while [[ $(jobs -r | wc -l) -ge $MAX_PARALLEL ]]; do
    sleep 0.5
  done
}

while read -r ip user pass; do
  if [[ -n "$LIMIT" ]]; then
    IFS=',' read -ra LIMIT_IPS <<< "$LIMIT"
    skip=true
    for lim_ip in "${LIMIT_IPS[@]}"; do
      [[ "$ip" == "$lim_ip" ]] && skip=false
    done
    $skip && continue
  fi

  sem
  worker "$ip" "$user" "$pass" &
done < "$IP_LIST"

wait

TOTAL_SUCCESS=$(wc -l < "$SUCCESS_FILE")
TOTAL_FAIL=$(wc -l < "$FAILED_FILE")

echo "🎯 全部任务执行完成: 成功 $TOTAL_SUCCESS 台，失败 $TOTAL_FAIL 台"
if [[ -s "$FAILED_FILE" ]]; then
  echo "❗ 失败主机列表已保存: $FAILED_FILE"
fi
