#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  init_vault_admin.sh --password <password> [options]

Options:
  --username <name>        Admin username. Default: admin
  --password <password>    Required. Password for the admin userpass account.
  --vault-addr <addr>      Vault API address. Default: http://127.0.0.1:8200
  --root-token <token>     Root token. Defaults to VAULT_TOKEN or
                           VAULT_SERVER_ROOT_ACCESS_TOKEN if set.
  --issuer <label>         TOTP issuer label. Default: Vault
  --method-name <name>     TOTP method name. Default: vault-admin-totp
  --output-dir <dir>       Enrollment output directory. Default: /tmp
  --ui-url <url>           UI login URL. Default: http://127.0.0.1:8200/ui/vault/auth?with=userpass
  -h, --help               Show this help message
EOF
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing required command: $1" >&2
    exit 1
  fi
}

b64decode() {
  if base64 --help 2>&1 | grep -q -- '--decode'; then
    base64 --decode
  else
    base64 -D
  fi
}

USERNAME="admin"
PASSWORD=""
VAULT_ADDR="${VAULT_ADDR:-http://127.0.0.1:8200}"
ROOT_TOKEN="${VAULT_TOKEN:-}"
if [[ -z "$ROOT_TOKEN" && -n "${VAULT_SERVER_ROOT_ACCESS_TOKEN:-}" ]]; then
  ROOT_TOKEN="${VAULT_SERVER_ROOT_ACCESS_TOKEN}"
fi
ISSUER="Vault"
METHOD_NAME="vault-admin-totp"
OUTPUT_DIR="/tmp"
UI_URL="http://127.0.0.1:8200/ui/vault/auth?with=userpass"
POLICY_NAME="vault-admins"
ENFORCEMENT_NAME="admin-userpass"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --username)
      USERNAME="${2:-}"
      shift 2
      ;;
    --password)
      PASSWORD="${2:-}"
      shift 2
      ;;
    --vault-addr)
      VAULT_ADDR="${2:-}"
      shift 2
      ;;
    --root-token)
      ROOT_TOKEN="${2:-}"
      shift 2
      ;;
    --issuer)
      ISSUER="${2:-}"
      shift 2
      ;;
    --method-name)
      METHOD_NAME="${2:-}"
      shift 2
      ;;
    --output-dir)
      OUTPUT_DIR="${2:-}"
      shift 2
      ;;
    --ui-url)
      UI_URL="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ -z "$PASSWORD" ]]; then
  echo "--password is required" >&2
  usage >&2
  exit 1
fi

if [[ -z "$ROOT_TOKEN" ]]; then
  echo "root token missing: pass --root-token or export VAULT_TOKEN first" >&2
  exit 1
fi

require_cmd vault
require_cmd jq
require_cmd curl
require_cmd base64

export VAULT_ADDR
export VAULT_TOKEN="$ROOT_TOKEN"

if ! vault status >/dev/null 2>&1; then
  echo "unable to reach Vault at $VAULT_ADDR" >&2
  exit 1
fi

if ! vault auth list -format=json | jq -e 'has("userpass/")' >/dev/null; then
  vault auth enable userpass >/dev/null
fi
vault auth tune -listing-visibility=unauth userpass/ >/dev/null

tmp_policy="$(mktemp)"
trap 'rm -f "$tmp_policy"' EXIT
cat >"$tmp_policy" <<'POL'
path "*" {
  capabilities = ["create", "read", "update", "delete", "list", "patch", "sudo"]
}
POL
vault policy write "$POLICY_NAME" "$tmp_policy" >/dev/null

vault write "auth/userpass/users/${USERNAME}" \
  password="$PASSWORD" \
  token_policies="$POLICY_NAME" >/dev/null

userpass_accessor="$(vault auth list -format=json | jq -r '."userpass/".accessor')"

methods_json="$(curl -sS \
  -H "X-Vault-Token: ${VAULT_TOKEN}" \
  -H "X-Vault-Request: true" \
  -X LIST \
  "${VAULT_ADDR}/v1/identity/mfa/method/totp")"
method_id="$(printf '%s' "$methods_json" | jq -r --arg method_name "$METHOD_NAME" '.data.key_info // {} | to_entries[]? | select(.value.name == $method_name) | .key' | head -n1)"

if [[ -z "$method_id" ]]; then
  method_json="$(vault write -format=json identity/mfa/method/totp \
    method_name="$METHOD_NAME" \
    issuer="$ISSUER" \
    period=30 \
    digits=6 \
    algorithm=SHA1 \
    skew=1 \
    max_validation_attempts=5)"
  method_id="$(printf '%s' "$method_json" | jq -r '.data.method_id // .data.id')"
fi

# Resolve the admin's identity entity WITHOUT logging in. Once the login MFA
# enforcement below exists, a userpass login is MFA-gated and returns no
# entity_id (causing "missing entityID" on every re-run). Instead look up the
# entity by name first, then fall back to the userpass entity-alias, creating
# the entity + alias only when needed.
entity_id=""
entity_json="$(vault read -format=json "identity/entity/name/${USERNAME}" 2>/dev/null || true)"
entity_id="$(printf '%s' "$entity_json" | jq -r '.data.id // empty')"

if [[ -z "$entity_id" ]]; then
  for alias_id in $(vault list -format=json identity/entity-alias/id 2>/dev/null | jq -r '.[]?'); do
    alias_json="$(vault read -format=json "identity/entity-alias/id/${alias_id}" 2>/dev/null || true)"
    alias_name="$(printf '%s' "$alias_json" | jq -r '.data.name // empty')"
    alias_mount="$(printf '%s' "$alias_json" | jq -r '.data.mount_accessor // empty')"
    if [[ "$alias_name" == "$USERNAME" && "$alias_mount" == "$userpass_accessor" ]]; then
      entity_id="$(printf '%s' "$alias_json" | jq -r '.data.canonical_id // empty')"
      break
    fi
  done
fi

if [[ -z "$entity_id" ]]; then
  entity_id="$(vault write -format=json identity/entity \
    name="$USERNAME" \
    policies="$POLICY_NAME" | jq -r '.data.id')"
fi

alias_exists=false
for alias_id in $(vault list -format=json identity/entity-alias/id 2>/dev/null | jq -r '.[]?'); do
  alias_json="$(vault read -format=json "identity/entity-alias/id/${alias_id}" 2>/dev/null || true)"
  alias_name="$(printf '%s' "$alias_json" | jq -r '.data.name // empty')"
  alias_mount="$(printf '%s' "$alias_json" | jq -r '.data.mount_accessor // empty')"
  if [[ "$alias_name" == "$USERNAME" && "$alias_mount" == "$userpass_accessor" ]]; then
    entity_id="$(printf '%s' "$alias_json" | jq -r '.data.canonical_id // empty')"
    alias_exists=true
    break
  fi
done

if [[ "$alias_exists" != true ]]; then
  vault write identity/entity-alias \
    name="$USERNAME" \
    canonical_id="$entity_id" \
    mount_accessor="$userpass_accessor" >/dev/null
fi

mkdir -p "$OUTPUT_DIR"
enrollment_json="${OUTPUT_DIR}/vault-${USERNAME}-totp.json"
enrollment_png="${OUTPUT_DIR}/vault-${USERNAME}-totp.png"
enrollment_uri="${OUTPUT_DIR}/vault-${USERNAME}-totp-uri.txt"

vault write identity/mfa/method/totp/admin-destroy \
  method_id="$method_id" \
  entity_id="$entity_id" >/dev/null 2>&1 || true

vault write -format=json identity/mfa/method/totp/admin-generate \
  method_id="$method_id" \
  entity_id="$entity_id" >"$enrollment_json"

jq -r '.data.barcode' "$enrollment_json" | b64decode >"$enrollment_png"
jq -r '.data.url' "$enrollment_json" >"$enrollment_uri"
chmod 600 "$enrollment_json" "$enrollment_png" "$enrollment_uri"

vault write "identity/mfa/login-enforcement/${ENFORCEMENT_NAME}" \
  mfa_method_ids="$method_id" \
  auth_method_accessors="$userpass_accessor" >/dev/null


cat <<EOF
vault_addr=$VAULT_ADDR
username=$USERNAME
policy=$POLICY_NAME
method_id=$method_id
userpass_accessor=$userpass_accessor
entity_id=$entity_id
enrollment_json=$enrollment_json
enrollment_png=$enrollment_png
enrollment_uri=$enrollment_uri
ui_url=$UI_URL
EOF
