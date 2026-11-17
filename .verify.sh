#!/bin/bash
cd "/Users/shenlan/workspaces/ai-workspace-infra/playbooks/.worktrees/obs-footprint"
bash -n roles/vhosts/vector-agent/files/bootstrap-remote-write-auth && echo "syntax OK"
T=$(mktemp -d)
cat > "$T/v.toml" <<'CFG'
[sinks.prometheus_remote.auth]
strategy = "basic"
user = "observability"
password = "Bearer uTvryFvAbz6M5sRtmTaSTQY6otLZ95hneBsWqXu+35I="
CFG
VECTOR_AUTH_STATIC_FILE="$T/o.json" VECTOR_CONFIG_PATH="$T/v.toml" VECTOR_AUTH_FILE_OWNER="$(id -un):$(id -gn)" \
  bash roles/vhosts/vector-agent/files/bootstrap-remote-write-auth >/dev/null
echo "--- 文件内容(脱敏) ---"
sed -E 's/(VECTOR_AUTH_PASSWORD=).*/\1<PW>/' "$T/o.json"
echo "--- bash source 还原测试 ---"
set -a; . "$T/o.json"; set +a
echo "USER=[$VECTOR_AUTH_USER]"
case "$VECTOR_AUTH_PASSWORD" in
  "Bearer "*) echo "PASSWORD 前缀正确, len=${#VECTOR_AUTH_PASSWORD}" ;;
  *) echo "PASSWORD 解析失败: [$VECTOR_AUTH_PASSWORD]" ;;
esac
rm -rf "$T"
