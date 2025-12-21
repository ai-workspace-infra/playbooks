#!/usr/bin/env bash
# deepflow/pull_save_scp_image_multi_arch.sh
# 远端：multi-arch pull（优先 --all-platforms，回退逐平台）
#   -> image convert (--oci --all-platforms) 到临时本地引用
#   -> save -o /tmp/<name>-<tag>.multi.tar (docker-archive)
#   -> scp 回本地
set -euo pipefail

REMOTE_HOST="${REMOTE_HOST:-root@10.1.3.179}"
DEST_DIR="${DEST_DIR:-$HOME/Desktop}"
REMOTE_TMPDIR="${REMOTE_TMPDIR:-/tmp}"
RM_REMOTE="${RM_REMOTE:-0}"

REMOTE_NERDCTL="${REMOTE_NERDCTL:-nerdctl}"
REMOTE_NERDCTL_NS="${REMOTE_NERDCTL_NS:-}"   # 例如 "k8s.io"
REMOTE_NC="${REMOTE_NERDCTL} ${REMOTE_NERDCTL_NS:+-n ${REMOTE_NERDCTL_NS}}"

PLATFORMS_DEFAULT="linux/amd64,linux/arm64"
PLATFORMS="${PLATFORMS:-$PLATFORMS_DEFAULT}"

usage() {
  cat <<EOF
用法:
  $0 <image1> [image2 ...] [--rm-remote]
  $0 -f images.txt [--rm-remote]

流程(远端):
  1) ${REMOTE_NC} pull --all-platforms <IMAGE>   # 不支持则逐平台 --platform
  2) ${REMOTE_NC} image convert --oci --all-platforms <IMAGE> <TARGET_REF>
  3) ${REMOTE_NC} save -o ${REMOTE_TMPDIR}/<name>-<tag>.multi.tar <TARGET_REF>
  4) scp 回本地 ${DEST_DIR}

环境变量:
  REMOTE_HOST, DEST_DIR, REMOTE_TMPDIR, REMOTE_NERDCTL, REMOTE_NERDCTL_NS, PLATFORMS, RM_REMOTE
EOF
}

# ---------- 参数解析 ----------
IMAGES=()
LIST_FILE=""
if [[ $# -eq 0 ]]; then usage; exit 1; fi

ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --rm-remote) RM_REMOTE=1; shift ;;
    -f)
      [[ $# -ge 2 ]] || { echo "❌ 缺少镜像清单文件"; exit 1; }
      LIST_FILE="$2"; shift 2 ;;
    *) ARGS+=("$1"); shift ;;
  esac
done

if [[ -n "$LIST_FILE" ]]; then
  [[ -f "$LIST_FILE" ]] || { echo "❌ 文件不存在: $LIST_FILE"; exit 1; }
  while IFS= read -r line; do
    line="${line%%#*}"; line="$(echo -n "$line" | xargs || true)"
    [[ -n "$line" ]] || continue
    IMAGES+=("$line")
  done < "$LIST_FILE"
fi
if [[ ${#ARGS[@]} -gt 0 ]]; then IMAGES+=("${ARGS[@]}"); fi
[[ ${#IMAGES[@]} -gt 0 ]] || { echo "❌ 没有可处理的镜像"; exit 1; }

echo "🖥️ 远端: $REMOTE_HOST"
echo "📂 本地目录: $DEST_DIR"
echo "🧭 命名空间: ${REMOTE_NERDCTL_NS:-<default>}"
echo "🧹 rm-remote: $([[ $RM_REMOTE -eq 1 ]] && echo ON || echo OFF)"
echo "🧩 回退平台: $PLATFORMS"
mkdir -p "$DEST_DIR"

# ---------- 预检查 ----------
ssh -o BatchMode=yes "$REMOTE_HOST" "command -v ${REMOTE_NERDCTL} >/dev/null" \
  || { echo "❌ 远端未安装 ${REMOTE_NERDCTL}"; exit 1; }
ssh -o BatchMode=yes "$REMOTE_HOST" "test -d ${REMOTE_TMPDIR}" \
  || { echo "❌ 远端临时目录不存在: ${REMOTE_TMPDIR}"; exit 1; }

REMOTE_SUPPORTS_ALL_PLATFORMS=0
if ssh -o BatchMode=yes "$REMOTE_HOST" "${REMOTE_NC} pull --help 2>/dev/null | grep -q -- '--all-platforms'"; then
  REMOTE_SUPPORTS_ALL_PLATFORMS=1
fi

# ---------- 工具函数 ----------
rand_suffix() { LC_ALL=C tr -dc a-z0-9 </dev/urandom | head -c 6; }

mk_target_ref() {
  local image="$1"
  local repo="${image%%[@:]*}"
  local suffix="${image#${repo}}"
  local tag="latest"
  if [[ "$suffix" == :* ]]; then
    tag="${suffix#:}"
  elif [[ "$suffix" == @* ]]; then
    tag="digest-$(echo "${suffix#@}" | cut -c1-12)"
  fi
  echo "${repo}:${tag}-oci-$(rand_suffix)"
}

process_image() {
  local IMAGE="$1"

  local NAME_TAG="${IMAGE##*/}"
  local NAME="${NAME_TAG%%[:@]*}"
  local TAG_OR_DIGEST="${NAME_TAG#${NAME}}"
  local TAG="latest"
  if [[ "$TAG_OR_DIGEST" == :* ]]; then
    TAG="${TAG_OR_DIGEST#:}"
  elif [[ "$TAG_OR_DIGEST" == @* ]]; then
    TAG="digest-$(echo "${TAG_OR_DIGEST#@}" | cut -c1-12)"
  fi

  local TARGET_REF; TARGET_REF="$(mk_target_ref "$IMAGE")"
  local REMOTE_TAR="${REMOTE_TMPDIR}/${NAME}-${TAG}.multi.tar"
  local DEST_PATH="${DEST_DIR}/${NAME}-${TAG}.multi.tar"

  echo
  echo "=============================="
  echo "📦 IMAGE       : $IMAGE"
  echo "🎯 TARGET_REF  : $TARGET_REF"
  echo "📁 REMOTE_TAR  : $REMOTE_TAR"
  echo "=============================="

  # 本地先把变量做 shell 安全转义，拼到远端命令里（避免引号问题）
  local Q_IMAGE Q_TARGET Q_TAR
  Q_IMAGE=$(printf %q "$IMAGE")
  Q_TARGET=$(printf %q "$TARGET_REF")
  Q_TAR=$(printf %q "$REMOTE_TAR")

  # 失败清理
  local CLEAN_ON_FAILURE=1
  trap 'if [[ "${CLEAN_ON_FAILURE:-0}" -eq 1 ]]; then
          ssh -o BatchMode=yes "'"$REMOTE_HOST"'" "rm -f '"$Q_TAR"'" || true
          ssh -o BatchMode=yes "'"$REMOTE_HOST"'" "'"${REMOTE_NC}"' rmi -f '"$Q_TARGET"' >/dev/null 2>&1 || true
        fi' RETURN

  # 1) 拉取多架构
  if [[ $REMOTE_SUPPORTS_ALL_PLATFORMS -eq 1 ]]; then
    ssh -o BatchMode=yes "$REMOTE_HOST" \
      "set -euo pipefail; ${REMOTE_NC} pull --all-platforms $Q_IMAGE"
  else
    echo "ℹ️ 远端不支持 --all-platforms，逐平台拉取: $PLATFORMS"
    IFS=, read -r -a arr <<< "$PLATFORMS"
    for p in "${arr[@]}"; do
      local QP; QP=$(printf %q "$p")
      ssh -o BatchMode=yes "$REMOTE_HOST" \
        "set -euo pipefail; ${REMOTE_NC} pull --platform=$QP $Q_IMAGE"
    done
  fi

  # 2) 转为 OCI（到临时本地引用），确保包含所有平台
  ssh -o BatchMode=yes "$REMOTE_HOST" \
    "set -euo pipefail; ${REMOTE_NC} image convert --oci --all-platforms $Q_IMAGE $Q_TARGET"

  # 3) 保存为 docker-archive TAR
  ssh -o BatchMode=yes "$REMOTE_HOST" \
    "set -euo pipefail; ${REMOTE_NC} save -o $Q_TAR $Q_TARGET"

  # 4) 回传
  scp -q "$REMOTE_HOST:$REMOTE_TAR" "$DEST_PATH"

  # 5) 清理
  if [[ $RM_REMOTE -eq 1 ]]; then
    ssh -o BatchMode=yes "$REMOTE_HOST" "rm -f $Q_TAR"
  fi
  ssh -o BatchMode=yes "$REMOTE_HOST" "${REMOTE_NC} rmi -f $Q_TARGET" >/dev/null 2>&1 || true

  CLEAN_ON_FAILURE=0
  trap - RETURN
  echo "✅ OK: $DEST_PATH (docker-archive, multi-arch)"
  echo "   加载：nerdctl load -i \"$DEST_PATH\""
  echo "   基本校验：tar tf \"$DEST_PATH\" | egrep 'manifest.json|repositories' | sed -n '1,5p'"
  echo "   平台确认(加载后)：nerdctl image inspect \"$TARGET_REF\" --mode=native | jq '.[0].Manifest.Manifests[].Platform'"
}

for img in "${IMAGES[@]}"; do
  process_image "$img"
done

echo
echo "🎉 全部 multi-arch 导出完成。"
