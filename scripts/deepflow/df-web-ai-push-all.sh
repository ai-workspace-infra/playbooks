#!/bin/bash
# df-web-ai-push-all.sh
# 从 <name>-<tag>.multi.tar (OCI) 逐个 load → retag → push 到目标仓库（multi-arch）
set -euo pipefail

LOCAL_REG="${LOCAL_REG:-sealos.hub:5000}"
NERDCTL_BIN="${NERDCTL_BIN:-nerdctl}"
NERDCTL_NS="${NERDCTL_NS:-}"           # 如 "k8s.io"
NC="${NERDCTL_BIN} ${NERDCTL_NS:+-n ${NERDCTL_NS}}"

usage() {
  cat <<EOF
用法:
  # 扫描当前目录 *.multi.tar 全部推送到 \${LOCAL_REG}/<name:tag>
  LOCAL_REG=sealos.hub:5000 $0

说明:
  - 对每个 tar:
      1) 解析 index.json，取 org.opencontainers.image.ref.name 作为源引用名（SRC_REF）
      2) nerdctl load -i <tar>
      3) 将 SRC_REF 重打为 \${LOCAL_REG}/<name:tag>
      4) nerdctl push \${LOCAL_REG}/<name:tag>   # multi-arch 一次性推送
EOF
}

if [[ "${1:-}" =~ ^-h|--help$ ]]; then usage; exit 0; fi

${NC} login "${LOCAL_REG}" || true

shopt -s nullglob
TARS=(*.multi.tar)
shopt -u nullglob
[[ ${#TARS[@]} -gt 0 ]] || { echo "⚠️ 未找到 *.multi.tar"; exit 0; }

get_src_ref_from_tar() {
  # 从 OCI index.json 提取 ref.name；优先 manifest 注解，其次 index 注解
  local tar="$1"
  local ref=""
  ref=$(tar -xOf "$tar" index.json 2>/dev/null | \
        jq -r '
          .manifests[0].annotations["org.opencontainers.image.ref.name"]
          // .annotations["org.opencontainers.image.ref.name"]
          // empty
        ')
  echo -n "$ref"
}

for TAR in "${TARS[@]}"; do
  echo
  echo "==> Processing $TAR"

  SRC_REF="$(get_src_ref_from_tar "$TAR")"
  if [[ -z "$SRC_REF" ]]; then
    # 回退：用文件名 <name>-<tag>.multi.tar 推导 name:tag
    BASE="$(basename "$TAR" .multi.tar)"
    if [[ "$BASE" != *:* ]]; then
      echo "❌ 无法从 $TAR 提取 SRC_REF，且文件名不含 <name:tag> 格式。请改名或使用包含 ref.name 的归档。"
      exit 2
    fi
    SRC_REF="$BASE"
    echo "ℹ️ 未找到 ref.name，使用文件名推导的源引用：$SRC_REF"
  else
    echo "SRC_REF: $SRC_REF"
  fi

  NAME_TAG="${SRC_REF##*/}"               # 仅保留 <name:tag>
  DEST="${LOCAL_REG}/${NAME_TAG}"
  echo "DEST:    $DEST"

  echo "==> Load $TAR"
  ${NC} load -i "$TAR"

  echo "==> Tag $SRC_REF -> $DEST"
  ${NC} tag "$SRC_REF" "$DEST"

  echo "==> Push $DEST (multi-arch)"
  ${NC} push "$DEST"

  echo "✅ DONE: $DEST"
done

echo
echo "All done. (multi-arch push)"
