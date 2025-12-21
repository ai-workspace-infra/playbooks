#!/bin/bash
set -e

print_help() {
  echo ""
  echo "📘 使用说明：k8s_backup_tool v4.15.16"
  echo ""
  echo "命令        说明"
  echo "backup      创建 K8s 应用资源备份 ➕ 节点数据打包并上传 S3"
  echo "restore <tag>  先恢复节点数据，再恢复 Velero 应用资源"
  echo "list        列出所有备份（Velero + S3），自动对齐 date_tag"
  echo "delete <tag> 删除指定 date_tag 的 Velero + S3 备份"
  echo ""
  echo "示例："
  echo "  bash $0 list -c k8s_backup_config.yaml"
  echo "  bash $0 backup -c k8s_backup_config.yaml"
  echo "  bash $0 delete -c k8s_backup_config.yaml <date_tag>"
  echo "  bash $0 restore -c k8s_backup_config.yaml <date_tag>"
  echo ""
}

install_depends() {
  echo "🔍 正在检查依赖项: jq, yq, velero, aws, rsync, tar"

  # 安装 AWS CLI v2（仅限 x86_64 Linux）
if ! command -v aws >/dev/null 2>&1; then
  echo "📦 正在安装 AWS CLI v2..."
  curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
  sudo apt install -y unzip || true
  unzip -q awscliv2.zip
  sudo ./aws/install
  rm -rf aws awscliv2.zip
  echo "✅ AWS CLI 安装完成：$(aws --version)"
else
  echo "✅ AWS CLI 已安装：$(aws --version)"
fi

  # 安装 jq
  if ! command -v jq >/dev/null 2>&1; then
    echo "❌ 缺少 jq，正在安装..."
    sudo apt-get update && sudo apt-get install -y jq || { echo "❌ 安装 jq 失败"; exit 1; }
  else
    echo "✅ jq 已安装：$(jq --version)"
  fi

  # 安装 yq（使用 mikefarah/yq 版本）
  if ! command -v yq >/dev/null 2>&1; then
    echo "❌ 缺少 yq，正在安装..."
    sudo wget https://github.com/mikefarah/yq/releases/latest/download/yq_linux_amd64 -O /usr/local/bin/yq
    sudo chmod +x /usr/local/bin/yq
  else
    echo "✅ yq 已安装：$(yq --version)"
  fi

  # 安装 velero
  if ! command -v velero >/dev/null 2>&1; then
    echo "❌ 缺少 velero，正在安装..."
    curl -fsSL https://github.com/vmware-tanzu/velero/releases/download/v1.15.2/velero-v1.15.2-linux-amd64.tar.gz -o velero.tar.gz
    tar -zxvf velero.tar.gz
    sudo mv velero*/velero /usr/local/bin/
    rm -rf velero* velero.tar.gz
  else
    echo "✅ velero 已安装：$(velero version --client-only)"
  fi

  echo "✅ 所有依赖项安装完成。"
}

check_dependencies() {
  echo "🔍 正在检查依赖项: jq, yq, velero, aws, rsync, tar"

  MISSING_DEPS=()

  for bin in jq yq velero aws rsync tar; do
    if ! command -v "$bin" &>/dev/null; then
      echo "❌ 缺少依赖：$bin"
      MISSING_DEPS+=("$bin")
    else
      echo "✅ $bin 已安装：$($bin --version 2>/dev/null | head -n 1 || echo OK)"
    fi
  done

  if [ ${#MISSING_DEPS[@]} -ne 0 ]; then
    echo ""
    echo "🛠 正在尝试自动安装以下依赖：${MISSING_DEPS[*]}"
    install_depends "${MISSING_DEPS[@]}"
  else
    echo "🎉 所有依赖项已就绪。"
  fi
}



log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') - $*"
}

load_config() {
  CONFIG_FILE="$1"
  [[ ! -f "$CONFIG_FILE" ]] && echo "❌ 找不到配置文件: $CONFIG_FILE" && exit 1

  VELERO_NAMESPACE=$(yq e '.settings.VELERO_NAMESPACE' "$CONFIG_FILE")
  VELERO_BUCKET=$(yq e '.settings.VELERO_BUCKET' "$CONFIG_FILE")
  VELERO_REGION=$(yq e '.settings.VELERO_REGION' "$CONFIG_FILE")
  AWS_ACCESS_KEY_ID=$(yq e '.settings.AWS_ACCESS_KEY_ID' "$CONFIG_FILE")
  AWS_SECRET_ACCESS_KEY=$(yq e '.settings.AWS_SECRET_ACCESS_KEY' "$CONFIG_FILE")
  export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY

  K8S_CLUSTER_NAME=$(yq e '.backup_config.cluster_name' "$CONFIG_FILE")
  TARGET_NAMESPACES=$(yq e '.backup_config.namespaces | join(",")' "$CONFIG_FILE")
  PRECMDS=$(yq e -r '.backup_config.precmds // ""' "$CONFIG_FILE")
  POSTCMDS=$(yq e -r '.backup_config.postcmds // ""' "$CONFIG_FILE")

  # 检查所有关键环境变量
  for var in VELERO_NAMESPACE VELERO_BUCKET VELERO_REGION AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY K8S_CLUSTER_NAME TARGET_NAMESPACES; do
    if [[ -z "${!var}" ]]; then
      log "❌ 环境变量 $var 未正确加载，请检查配置文件！"
      exit 1
    fi
  done

  declare -gA NODE_BACKUP_PATHS
  local nodes_count
  nodes_count=$(yq e '.backup_config.nodes | length' "$CONFIG_FILE")
  for (( i=0; i<nodes_count; i++ )); do
    local key value
    key=$(yq e ".backup_config.nodes | keys | .[$i]" "$CONFIG_FILE")
    value=$(yq e ".backup_config.nodes[\"$key\"]" "$CONFIG_FILE")
    NODE_BACKUP_PATHS["$key"]="$value"
  done

  # DEBUG 检查节点路径
  log "🔍 已加载节点备份配置:"
  for NODE in "${!NODE_BACKUP_PATHS[@]}"; do
    log "节点 [$NODE]: 路径 [${NODE_BACKUP_PATHS[$NODE]}]"
  done

  # 检查节点配置是否为空
  if [[ ${#NODE_BACKUP_PATHS[@]} -eq 0 ]]; then
    log "❌ 配置文件中缺少节点备份路径 (backup_config.nodes)，请检查配置文件！"
    exit 1
  fi
}


backup_all() {
  DATE_TAG=$(date "+%Y%m%d%H%M")
  BACKUP_NAME="${K8S_CLUSTER_NAME}-backup-${DATE_TAG}-$(head /dev/urandom | tr -dc a-z0-9 | head -c4)"
  S3_NODE_PATH="s3://${VELERO_BUCKET}/${K8S_CLUSTER_NAME}/${DATE_TAG}/"

  log "🆔 date_tag: $DATE_TAG"
  log "📛 velero_backup_name: $BACKUP_NAME"

  TMP_DIR="/tmp/k8s-node-backup"
  mkdir -p "$TMP_DIR"
  rm -rf "$TMP_DIR"/*

  if [[ -n "$PRECMDS" ]]; then
    log "🔧 执行预备命令（precmds）..."
    bash -c "$PRECMDS" || {
      echo "❌ precmds 执行失败，中止备份"
      exit 1
    }
  fi

  log "📦 创建 Velero 应用资源备份..."
  velero backup create "$BACKUP_NAME" \
    --namespace "$VELERO_NAMESPACE" \
    --include-namespaces "$TARGET_NAMESPACES" \
    --ttl 240h \
    --labels "cluster=${K8S_CLUSTER_NAME},date_tag=${DATE_TAG}"

# 检查节点数量
if [[ ${#NODE_BACKUP_PATHS[@]} -eq 0 ]]; then
  log "❌ 没有配置节点数据备份路径，请检查配置文件！"
  exit 1
fi

log "🔄 开始执行节点数据备份循环，共有节点数: ${#NODE_BACKUP_PATHS[@]}"

for NODE in "${!NODE_BACKUP_PATHS[@]}"; do
  SRC_PATH="${NODE_BACKUP_PATHS[$NODE]}"
  SNAPSHOT_DIR="${TMP_DIR}/${NODE}"
  log "🔹 正在备份节点 [$NODE]，路径为 [$SRC_PATH] 到 [$SNAPSHOT_DIR]..."

  mkdir -p "$SNAPSHOT_DIR"

  if rsync -aHAX --numeric-ids "$SRC_PATH/" "$SNAPSHOT_DIR/"; then
    log "✅ rsync 同步完成 [$NODE]"
  else
    log "❌ rsync 同步失败 [$NODE]"
    continue
  fi

  ARCHIVE="${TMP_DIR}/${NODE}_backup_path.tar.xz"
  if tar --preserve-permissions --same-owner -cJf "$ARCHIVE" -C "$TMP_DIR" "$NODE"; then
    log "✅ 压缩归档成功: $ARCHIVE"
  else
    log "❌ 压缩归档失败: $ARCHIVE"
    continue
  fi

  md5sum "$ARCHIVE" > "${ARCHIVE}.md5"
  log "📤 上传节点数据到 S3 [$S3_NODE_PATH]..."

  aws s3 cp "$ARCHIVE" "$S3_NODE_PATH"
  aws s3 cp "${ARCHIVE}.md5" "$S3_NODE_PATH"

  log "✅ 节点 [$NODE] 数据已成功上传到 S3"
done

log "🔄 节点数据备份循环执行完成"


  if [[ -n "$POSTCMDS" ]]; then
    log "🔧 执行后续命令（postcmds）..."
    bash -c "$POSTCMDS"
  fi

  log "✅ 备份完成，Velero + 节点数据已同步到 S3"
}


delete_backup() {
  DELETE_TAG="$1"
  [[ -z "$K8S_CLUSTER_NAME" || -z "$VELERO_NAMESPACE" ]] && echo "❌ 缺失 K8S_CLUSTER_NAME 或 VELERO_NAMESPACE" && exit 1

  log "🔍 查找 date_tag=${DELETE_TAG} 的 Velero 备份 (cluster=${K8S_CLUSTER_NAME})"

  # 预加载 JSON，避免 selector 语法错误
  BACKUP_JSON=$(velero backup get --namespace "$VELERO_NAMESPACE" -o json)
  BACKUP_NAME=$(echo "$BACKUP_JSON" | jq -r \
    --arg dt "$DELETE_TAG" \
    --arg cluster "$K8S_CLUSTER_NAME" '
    .items[] | select(
      .metadata.labels.cluster == $cluster and
      .metadata.labels.date_tag == $dt
    ) | .metadata.name'
  )

  if [[ "$BACKUP_NAME" == "null" || -z "$BACKUP_NAME" ]]; then
    echo "❌ 没有找到指定 date_tag 的 Velero 备份"
    echo "📋 当前 Velero 备份标签如下："
    echo "$BACKUP_JSON" | jq -r '
      .items[] | [.metadata.name, .metadata.labels.cluster, .metadata.labels.date_tag] | @tsv' | column -t
    exit 1
  fi

  log "🗑️ 删除 Velero 备份：$BACKUP_NAME"
  velero backup delete "$BACKUP_NAME" --namespace "$VELERO_NAMESPACE" --confirm

  log "🧹 删除 S3 节点数据：s3://${VELERO_BUCKET}/${K8S_CLUSTER_NAME}/${DELETE_TAG}/"
  aws s3 rm "s3://${VELERO_BUCKET}/${K8S_CLUSTER_NAME}/${DELETE_TAG}/" --recursive --region "$VELERO_REGION"
  log "✅ 删除完成"
}

restore_backup() {
  DATE_TAG="$1"
  BACKUP_NAME=$(velero backup get --namespace "$VELERO_NAMESPACE" -o json | jq -r \
    --arg dt "$DATE_TAG" \
    --arg cluster "$K8S_CLUSTER_NAME" \
    '.items[] | select(.metadata.labels.cluster == $cluster and .metadata.labels.date_tag == $dt) | .metadata.name' | head -n1)

  if [[ "$BACKUP_NAME" == "null" || -z "$BACKUP_NAME" ]]; then
    log "❌ 无法找到 Velero 备份: date_tag=$DATE_TAG, cluster=$K8S_CLUSTER_NAME"
    velero backup get --namespace "$VELERO_NAMESPACE" --show-labels
    exit 1
  fi

  TMP_DIR="/var/backups/k8s-restore/${DATE_TAG}"
  mkdir -p "$TMP_DIR"
  TMP_DIR="$(cd "$TMP_DIR"; pwd)"

  if [[ "$TMP_DIR" != /var/backups/k8s-restore/* ]]; then
    log "❌ 临时目录路径异常，安全退出: $TMP_DIR"
    exit 1
  fi

  rm -rf "${TMP_DIR:?}"/*

  if [[ -n "$PRECMDS" ]]; then
    log "🔧 执行预备命令（precmds）..."
    bash -c "$PRECMDS" || {
      log "❌ precmds 执行失败，中止恢复"
      exit 1
    }
  fi

  for NODE in "${!NODE_BACKUP_PATHS[@]}"; do
    DEST_PATH="${NODE_BACKUP_PATHS[$NODE]}"
    ARCHIVE_NAME="${NODE}_backup_path.tar.xz"
    ARCHIVE_PATH="${TMP_DIR}/${ARCHIVE_NAME}"
    EXTRACT_DIR="${TMP_DIR}/extracted/${NODE}"

    log "📦 下载 ${ARCHIVE_NAME} 到本地临时目录..."
    aws s3 cp "s3://${VELERO_BUCKET}/${K8S_CLUSTER_NAME}/${DATE_TAG}/${ARCHIVE_NAME}" "$ARCHIVE_PATH"

    log "📂 解压到 $EXTRACT_DIR..."
    mkdir -p "$EXTRACT_DIR"
    tar --preserve-permissions --same-owner -xJf "$ARCHIVE_PATH" -C "$EXTRACT_DIR"

    log "🔁 使用 rsync 同步到目标路径 $DEST_PATH..."
    rsync -aAXH --numeric-ids "${EXTRACT_DIR}/${NODE}/" "$DEST_PATH/"

    log "✅ 节点 [$NODE] 数据恢复完成"
  done

  log "♻️ 恢复 Velero 应用资源..."
  velero restore create --from-backup "$BACKUP_NAME" --namespace "$VELERO_NAMESPACE"

  if [[ -n "$POSTCMDS" ]]; then
    log "🔧 执行后续命令（postcmds）..."
    bash -c "$POSTCMDS"
  fi

  log "✅ 恢复完成"
}

list_backups() {
  echo "📦 k8s APP 应用资源备份（cluster=$K8S_CLUSTER_NAME）:"
  velero backup get --namespace "$VELERO_NAMESPACE" -o json | jq -r '
    .items[] | select(.metadata.labels.cluster == "'"$K8S_CLUSTER_NAME"'") |
    [.metadata.labels.date_tag, .metadata.name, .status.phase] | @tsv' | column -t

  echo ""
  echo "☁️ k8s Node 数据备份："
  aws s3 ls "s3://${VELERO_BUCKET}/${K8S_CLUSTER_NAME}/" --recursive | grep '.tar.xz' |
  awk -F '/' '{print $(NF-1)}' | sort -u | while read -r tag; do
    TOTAL=$(aws s3 ls "s3://${VELERO_BUCKET}/${K8S_CLUSTER_NAME}/${tag}/" --recursive | awk '{sum+=$3} END{printf "%.1f MiB", sum/1024/1024}')
    echo "📁 $tag   $TOTAL   $K8S_CLUSTER_NAME"
  done
}

### 主程序入口 ###
### 主程序入口 ###
ACTION=""
CONFIG_FILE=""
DEBUG_MODE="off"
DATE_TAG=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    backup|restore|list|delete)
      ACTION="$1"
      shift
      ;;
    -c|--config)
      CONFIG_FILE="$2"
      shift 2
      ;;
    --debug)
      DEBUG_MODE="on"
      shift
      ;;
    *)
      DATE_TAG="$1"
      shift
      ;;
  esac
done

if [[ -z "$ACTION" || -z "$CONFIG_FILE" ]]; then
  print_help
  exit 1
fi

check_dependencies
load_config "$CONFIG_FILE"

# 开启DEBUG模式（如果实现的话）
if [[ "$DEBUG_MODE" == "on" ]]; then
  set -x
fi

case "$ACTION" in
  backup)
    backup_all
    ;;
  delete)
    delete_backup "$DATE_TAG"
    ;;
  restore)
    restore_backup "$DATE_TAG"
    ;;
  list)
    list_backups
    ;;
  *)
    print_help
    ;;
esac
