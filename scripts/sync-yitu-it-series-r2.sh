#!/usr/bin/env bash
set -euo pipefail

LOCAL_SRC_DEFAULT="/Users/shenlan/Library/CloudStorage/GoogleDrive-haitaopanhq@gmail.com/我的云端硬盘/自媒体"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLAYBOOKS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOCAL_SRC="${LOCAL_SRC:-$LOCAL_SRC_DEFAULT}"
R2_BUCKET="${R2_BUCKET:-yitu-it-series}"
R2_REMOTE="${R2_REMOTE:-cloudflare-r2}"
R2_CUSTOM_DOMAIN="${R2_CUSTOM_DOMAIN:-img.svc.plus}"
RCLONE_BIN="${RCLONE_BIN:-rclone}"
WRANGLER_BIN="${WRANGLER_BIN:-npx --yes wrangler@latest}"
LOG_FILE="${LOG_FILE:-$HOME/rclone-yitu-it-series.log}"

SYNC_FLAGS=(
  --progress
  --stats 30s
  --transfers "${RCLONE_TRANSFERS:-16}"
  --checkers "${RCLONE_CHECKERS:-32}"
  --fast-list
  --s3-upload-cutoff "${RCLONE_S3_UPLOAD_CUTOFF:-128M}"
  --s3-chunk-size "${RCLONE_S3_CHUNK_SIZE:-128M}"
  --retries "${RCLONE_RETRIES:-8}"
  --low-level-retries "${RCLONE_LOW_LEVEL_RETRIES:-20}"
  --timeout "${RCLONE_TIMEOUT:-5m}"
  --contimeout "${RCLONE_CONTIMEOUT:-30s}"
  --exclude ".DS_Store"
  --exclude "Icon?"
  --exclude "._*"
  --exclude ".Spotlight-V100/**"
  --exclude ".Trashes/**"
  --log-file "$LOG_FILE"
  --log-level "${RCLONE_LOG_LEVEL:-INFO}"
)

usage() {
  cat <<'EOF'
Usage:
  sync-yitu-it-series-r2.sh doctor
  sync-yitu-it-series-r2.sh create-bucket
  sync-yitu-it-series-r2.sh configure-rclone
  sync-yitu-it-series-r2.sh dry-run
  sync-yitu-it-series-r2.sh sync
  sync-yitu-it-series-r2.sh copy
  sync-yitu-it-series-r2.sh check
  sync-yitu-it-series-r2.sh tree
  sync-yitu-it-series-r2.sh configure-custom-domain
  sync-yitu-it-series-r2.sh install-launchd
  sync-yitu-it-series-r2.sh uninstall-launchd

Required for configure-rclone:
  CF_ACCOUNT_ID or CLOUDFLARE_ACCOUNT_ID
  R2_ACCESS_KEY_ID
  R2_SECRET_ACCESS_KEY

Required for create-bucket:
  CLOUDFLARE_API_TOKEN or an active Wrangler login

Required for configure-custom-domain:
  CF_ACCOUNT_ID or CLOUDFLARE_ACCOUNT_ID
  CF_ZONE_ID or CLOUDFLARE_ZONE_ID
  CLOUDFLARE_API_TOKEN
EOF
}

require_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Missing command: $cmd" >&2
    exit 1
  fi
}

account_id() {
  printf '%s' "${CF_ACCOUNT_ID:-${CLOUDFLARE_ACCOUNT_ID:-}}"
}

zone_id() {
  printf '%s' "${CF_ZONE_ID:-${CLOUDFLARE_ZONE_ID:-}}"
}

require_env() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "Missing environment variable: $name" >&2
    exit 1
  fi
}

require_account_id() {
  if [[ -z "$(account_id)" ]]; then
    echo "Missing CF_ACCOUNT_ID or CLOUDFLARE_ACCOUNT_ID" >&2
    exit 1
  fi
}

remote_endpoint() {
  require_account_id
  printf 'https://%s.r2.cloudflarestorage.com' "$(account_id)"
}

doctor() {
  require_cmd "$RCLONE_BIN"
  echo "Local source: $LOCAL_SRC"
  test -d "$LOCAL_SRC"
  "$RCLONE_BIN" version | sed -n '1,8p'
  "$RCLONE_BIN" size "$LOCAL_SRC" \
    --exclude ".DS_Store" \
    --exclude "Icon?" \
    --exclude "._*"
  echo
  echo "Rclone remotes:"
  "$RCLONE_BIN" listremotes || true
  echo
  echo "Target: ${R2_REMOTE}:${R2_BUCKET}/"
}

create_bucket() {
  require_account_id
  if [[ -n "${CLOUDFLARE_API_TOKEN:-}" ]]; then
    CLOUDFLARE_ACCOUNT_ID="$(account_id)" CLOUDFLARE_API_TOKEN="$CLOUDFLARE_API_TOKEN" \
      $WRANGLER_BIN r2 bucket create "$R2_BUCKET"
  else
    CLOUDFLARE_ACCOUNT_ID="$(account_id)" $WRANGLER_BIN r2 bucket create "$R2_BUCKET"
  fi
}

configure_rclone() {
  require_account_id
  require_env R2_ACCESS_KEY_ID
  require_env R2_SECRET_ACCESS_KEY
  "$RCLONE_BIN" config create "$R2_REMOTE" s3 \
    provider Cloudflare \
    access_key_id "$R2_ACCESS_KEY_ID" \
    secret_access_key "$R2_SECRET_ACCESS_KEY" \
    endpoint "$(remote_endpoint)" \
    region auto \
    acl private \
    no_check_bucket true
  "$RCLONE_BIN" lsd "${R2_REMOTE}:"
}

sync_dry_run() {
  "$RCLONE_BIN" sync "$LOCAL_SRC" "${R2_REMOTE}:${R2_BUCKET}/" --dry-run "${SYNC_FLAGS[@]}"
}

sync_run() {
  "$RCLONE_BIN" sync "$LOCAL_SRC" "${R2_REMOTE}:${R2_BUCKET}/" "${SYNC_FLAGS[@]}"
}

copy_run() {
  "$RCLONE_BIN" copy "$LOCAL_SRC" "${R2_REMOTE}:${R2_BUCKET}/" "${SYNC_FLAGS[@]}"
}

check_run() {
  "$RCLONE_BIN" check "$LOCAL_SRC" "${R2_REMOTE}:${R2_BUCKET}/" \
    --one-way \
    --size-only \
    --exclude ".DS_Store" \
    --exclude "Icon?" \
    --exclude "._*"
}

tree_run() {
  "$RCLONE_BIN" tree "${R2_REMOTE}:${R2_BUCKET}/" --max-depth "${TREE_MAX_DEPTH:-2}"
}

configure_custom_domain() {
  require_account_id
  require_env CLOUDFLARE_API_TOKEN
  local zid
  zid="$(zone_id)"
  if [[ -z "$zid" ]]; then
    echo "Missing CF_ZONE_ID or CLOUDFLARE_ZONE_ID" >&2
    exit 1
  fi
  curl -fsS "https://api.cloudflare.com/client/v4/accounts/$(account_id)/r2/buckets/${R2_BUCKET}/domains/custom" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
    -d "{
      \"domain\": \"${R2_CUSTOM_DOMAIN}\",
      \"enabled\": true,
      \"zoneId\": \"${zid}\",
      \"minTLS\": \"1.2\"
    }" | jq .
}

install_launchd() {
  local plist="$HOME/Library/LaunchAgents/plus.svc.yitu-it-series.rclone-sync.plist"
  mkdir -p "$HOME/Library/LaunchAgents"
  cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "https://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>plus.svc.yitu-it-series.rclone-sync</string>
    <key>ProgramArguments</key>
    <array>
      <string>$SCRIPT_DIR/sync-yitu-it-series-r2.sh</string>
      <string>sync</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$PLAYBOOKS_DIR</string>
    <key>StartInterval</key>
    <integer>${LAUNCHD_START_INTERVAL:-1800}</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$HOME/rclone-yitu-it-series.launchd.out.log</string>
    <key>StandardErrorPath</key>
    <string>$HOME/rclone-yitu-it-series.launchd.err.log</string>
  </dict>
</plist>
EOF
  launchctl unload "$plist" >/dev/null 2>&1 || true
  launchctl load "$plist"
  launchctl start plus.svc.yitu-it-series.rclone-sync
  echo "Installed $plist"
}

uninstall_launchd() {
  local plist="$HOME/Library/LaunchAgents/plus.svc.yitu-it-series.rclone-sync.plist"
  launchctl unload "$plist" >/dev/null 2>&1 || true
  rm -f "$plist"
  echo "Removed $plist"
}

case "${1:-}" in
  doctor) doctor ;;
  create-bucket) create_bucket ;;
  configure-rclone) configure_rclone ;;
  dry-run) sync_dry_run ;;
  sync) sync_run ;;
  copy) copy_run ;;
  check) check_run ;;
  tree) tree_run ;;
  configure-custom-domain) configure_custom_domain ;;
  install-launchd) install_launchd ;;
  uninstall-launchd) uninstall_launchd ;;
  -h|--help|help|"") usage ;;
  *) usage; exit 2 ;;
esac
