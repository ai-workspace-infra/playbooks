#!/bin/bash

set -e

####################################
# ������ 配置区
####################################

IP_LIST="./ip.list"
SERVICE_NAME="deepflow-agent"
PKG_DIR="deepflow-agent-for-linux"
MAX_PARALLEL=5

CONTROLLER_IP=""
VTAP_GROUP_ID=""
LIMIT=""
SUDO_MODE="sudo"  # 可选: sudo | sudo-i

SSH_OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=15"

FAILED_FILE="failed_hosts.txt"
SUCCESS_FILE="success_hosts.txt"
> "$FAILED_FILE"
> "$SUCCESS_FILE"

####################################
# 参数解析（新增 --sudo-mode）
####################################

if [[ $# -eq 0 ]]; then
  echo "用法: $0 {deploy|upgrade|verify} --controller <ip> --group <id> [--limit ip1,ip2] [--sudo-mode sudo|sudo-i]"
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
    --sudo-mode)
      case "$2" in
        sudo|sudo-i)
          SUDO_MODE="$2"
          shift 2
          ;;
        *)
          echo "❌ --sudo-mode 必须是 'sudo' 或 'sudo-i'"
          exit 1
          ;;
      esac
      ;;
    *)
      echo "未知参数: $1"
      exit 1
      ;;
  esac
done

if [[ "$ACTION" != "deploy" && "$ACTION" != "upgrade" && "$ACTION" != "verify" ]]; then
  echo "用法: $0 {deploy|upgrade|verify} --controller <ip> --group <id> [--limit ip1,ip2] [--sudo-mode sudo|sudo-i]"
  exit 1
fi

if [[ "$ACTION" != "verify" && ( -z "$CONTROLLER_IP" || -z "$VTAP_GROUP_ID" ) ]]; then
  echo "❗ deploy/upgrade 必须传入 --controller 和 --group 参数"
  exit 1
fi

####################################
# 核心函数（重点修改：SUDO 处理 + 重启逻辑）
####################################

worker() {
  local ip="$1"
  local user="$2"
  local pass="$3"

  echo "������ [$ACTION] 处理主机 $ip ($user)"

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

  # 安装 + 配置
  if install_agent "$ip" "$user" "$pass" "$pkg_path" && update_config "$ip" "$user" "$pass"; then
    # ✅ 配置完成后，再次重启服务，确保新配置生效
    restart_agent_service "$ip" "$user" "$pass" && {
      echo "✅ $ip $ACTION 完成"
      echo "$ip" >> "$SUCCESS_FILE"
    } || {
      echo "❌ $ip 服务重启失败"
      echo "$ip" >> "$FAILED_FILE"
    }
  else
    echo "❌ $ip 安装或配置失败"
    echo "$ip" >> "$FAILED_FILE"
  fi

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
    patterns=("$PKG_DIR"/deepflow-agent-*.$init-arm.* \
              "$PKG_DIR"/deepflow-agent-*.$init-arm64.* \
              "$PKG_DIR"/deepflow-agent-*.$init-aarch64.*)
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
    echo "������ 选择安装包: $latest" >&2
    echo "$latest"
  else
    echo "UNSUPPORTED"
  fi
}

# ✅ 修改 install_agent：支持 sudo 和 sudo-i
install_agent() {
  local ip="$1" user="$2" pass="$3" pkg_path="$4"
  local remote_pkg="/tmp/agent.${pkg_path##*.}"

  sshpass -p "$pass" scp $SSH_OPTS "$pkg_path" "$user@$ip:$remote_pkg" || {
    echo "❌ $ip 上传安装包失败"
    return 1
  }

  # 构建 SUDO 前缀
  local SUDO_CMD=""
  case "$SUDO_MODE" in
    sudo)
      SUDO_CMD="sudo"
      ;;
    sudo-i)
      SUDO_CMD="sudo -i"
      ;;
    *)
      SUDO_CMD="sudo"
      ;;
  esac

  sshpass -p "$pass" ssh $SSH_OPTS "$user@$ip" bash <<EOF
set -e
if command -v sudo >/dev/null; then SUDO="sudo"; else SUDO=""; fi

# 使用指定模式
SUDO_MODE_CMD='$SUDO_CMD'

echo "������ 使用权限模式: \$SUDO_MODE_CMD"

if [[ "$remote_pkg" == *.rpm ]]; then
  \$SUDO_MODE_CMD rpm -Uvh --replacepkgs "$remote_pkg"
elif [[ "$remote_pkg" == *.deb ]]; then
  \$SUDO_MODE_CMD dpkg -i "$remote_pkg" || \$SUDO_MODE_CMD apt-get install -f -y
else
  echo "❌ 不支持的安装包格式"
  exit 1
fi

# 服务管理（注意：sudo -i 下 systemctl 可能仍可用）
if command -v systemctl &>/dev/null; then
  \$SUDO_MODE_CMD systemctl enable $SERVICE_NAME
  \$SUDO_MODE_CMD systemctl restart $SERVICE_NAME
elif command -v service &>/dev/null; then
  \$SUDO_MODE_CMD service $SERVICE_NAME restart
  \$SUDO_MODE_CMD chkconfig $SERVICE_NAME on
elif command -v initctl &>/dev/null; then
  \$SUDO_MODE_CMD initctl restart $SERVICE_NAME || \$SUDO_MODE_CMD initctl start $SERVICE_NAME
else
  echo "❌ 无法识别服务管理方式"
  exit 1
fi
EOF
}

# ✅ 修改 update_config：确保配置写入 /etc/
update_config() {
  local ip="$1" user="$2" pass="$3"
  local SUDO_CMD=""
  case "$SUDO_MODE" in
    sudo)
      SUDO_CMD="sudo"
      ;;
    sudo-i)
      SUDO_CMD="sudo -i"
      ;;
    *)
      SUDO_CMD="sudo"
      ;;
  esac

  sshpass -p "$pass" ssh $SSH_OPTS "$user@$ip" bash <<EOF
set -e
SUDO_MODE_CMD='$SUDO_CMD'
CONFIG_FILE="/etc/deepflow-agent.yaml"

# 使用 sudo -i 创建目录（更可靠）
\$SUDO_MODE_CMD mkdir -p \$(dirname \$CONFIG_FILE)

# 写入配置（使用 tee 避免重定向权限问题）
cat <<'CFG' | \$SUDO_MODE_CMD tee "\$CONFIG_FILE" >/dev/null
controller-ips:
  - $CONTROLLER_IP
vtap-group-id: "$VTAP_GROUP_ID"
CFG

\$SUDO_MODE_CMD chmod 644 "\$CONFIG_FILE"
\$SUDO_MODE_CMD chown root:root "\$CONFIG_FILE"
EOF
}

# ✅ 新增函数：服务重启 + 状态检查
restart_agent_service() {
  local ip="$1" user="$2" pass="$3"
  local SUDO_CMD=""
  case "$SUDO_MODE" in
    sudo)
      SUDO_CMD="sudo"
      ;;
    sudo-i)
      SUDO_CMD="sudo -i"
      ;;
    *)
      SUDO_CMD="sudo"
      ;;
  esac

  sshpass -p "$pass" ssh $SSH_OPTS "$user@$ip" bash <<EOF
set -e
SUDO_MODE_CMD='$SUDO_CMD'

# 重启服务
echo "������ 正在重启 deepflow-agent.service 以应用配置..."
\$SUDO_MODE_CMD systemctl restart $SERVICE_NAME
sleep 3

# 检查服务状态
if ! \$SUDO_MODE_CMD systemctl is-active $SERVICE_NAME > /dev/null 2>&1; then
  echo "❌ deepflow-agent.service 重启后未运行"
  exit 1
fi

echo "✅ deepflow-agent.service 已成功重启"
EOF
}

verify_agent() {
  local ip="$1" user="$2" pass="$3"
  echo "������ $ip 状态检查："
  sshpass -p "$pass" ssh $SSH_OPTS "$user@$ip" "
    sudo systemctl is-active $SERVICE_NAME 2>/dev/null || \
    sudo service $SERVICE_NAME status || \
    sudo initctl status $SERVICE_NAME || \
    echo '⚠️ 服务状态未知'
  "
}

####################################
# 并发控制主逻辑（不变）
####################################

sem(){
  while [[ $(jobs -r | wc -l) -ge $MAX_PARALLEL ]]; do
    sleep 0.5
  done
}

while read -r ip user pass; do
  [[ -z "$ip" || "$ip" =~ ^# ]] && continue

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

echo "������ 全部任务执行完成: 成功 $TOTAL_SUCCESS 台，失败 $TOTAL_FAIL 台"
if [[ -s "$FAILED_FILE" ]]; then
  echo "❗ 失败主机列表已保存: $FAILED_FILE"
fi
