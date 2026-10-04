#!/usr/bin/env bash
set -euo pipefail
umask 077

usage() {
  cat <<'EOF'
Usage:
  VAULT_TOKEN=<operator-token> VAULT_ADMIN_PASSWORD=<password> init_vault_admin.sh [options]

Options:
  --username <name>        Admin username. Default: admin
  --vault-addr <addr>      Vault API address. Default: http://127.0.0.1:8200
  --issuer <label>         TOTP issuer label. Default: Vault
  --method-name <name>     TOTP method name. Default: vault-admin-totp
  --output-dir <dir>       Private enrollment directory. Default: ~/.local/share/vault-admin-enrollment
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
PASSWORD="${VAULT_ADMIN_PASSWORD:-}"
VAULT_ADDR="${VAULT_ADDR:-http://127.0.0.1:8200}"
ROOT_TOKEN="${VAULT_TOKEN:-}"
ISSUER="Vault"
METHOD_NAME="vault-admin-totp"
OUTPUT_DIR="${HOME}/.local/share/vault-admin-enrollment"
UI_URL="http://127.0.0.1:8200/ui/vault/auth?with=userpass"
POLICY_NAME="vault-admins"
ENFORCEMENT_NAME="admin-userpass"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --username)
      USERNAME="${2:-}"
      shift 2
      ;;
    --vault-addr)
      VAULT_ADDR="${2:-}"
      shift 2
      ;;
    --password|--root-token)
      echo "Refusing secret command-line arguments; set VAULT_ADMIN_PASSWORD and VAULT_TOKEN in a protected operator session." >&2
      exit 2
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
  echo "VAULT_ADMIN_PASSWORD is required" >&2
  usage >&2
  exit 1
fi

if [[ -z "$ROOT_TOKEN" ]]; then
  echo "VAULT_TOKEN is required in the operator session" >&2
  exit 1
fi
if [[ ! "$USERNAME" =~ ^[a-zA-Z0-9_-]+$ ]]; then
  echo "username must contain only letters, digits, _ or -" >&2
  exit 2
fi
case "${OUTPUT_DIR%/}" in
  /|/tmp|/var/tmp|/root|"${HOME}")
    echo "--output-dir must be a dedicated private directory, not a shared or home root" >&2
    exit 2
    ;;
esac
if [[ -L "${OUTPUT_DIR}" ]]; then
  echo "--output-dir must not be a symlink" >&2
  exit 2
fi

require_cmd vault
require_cmd jq
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

if ! vault secrets list -format=json | jq -e 'has("kv/")' >/dev/null; then
  vault secrets enable -version=2 kv >/dev/null
fi

tmp_policy="$(mktemp)"
trap 'rm -f "$tmp_policy"' EXIT
cat >"$tmp_policy" <<'POL'
path "*" {
  capabilities = ["create", "read", "update", "delete", "list", "patch", "sudo"]
}
POL
vault policy write "$POLICY_NAME" "$tmp_policy" >/dev/null

if ! vault read "auth/userpass/users/${USERNAME}" >/dev/null 2>&1; then
  vault write "auth/userpass/users/${USERNAME}" \
    password="$PASSWORD" \
    token_policies="$POLICY_NAME" >/dev/null
fi

userpass_accessor="$(vault auth list -format=json | jq -r '."userpass/".accessor')"

methods_json="$(vault list -format=json identity/mfa/method/totp 2>/dev/null || printf '[]')"
method_id=""
while IFS= read -r candidate; do
  [[ -n "$candidate" ]] || continue
  candidate_json="$(vault read -format=json "identity/mfa/method/totp/${candidate}" 2>/dev/null || true)"
  candidate_name="$(printf '%s' "$candidate_json" | jq -r '.data.method_name // .data.name // empty')"
  if [[ "$candidate_name" == "$METHOD_NAME" ]]; then
    method_id="$candidate"
    break
  fi
done < <(printf '%s' "$methods_json" | jq -r '.[]?')

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

mkdir -p -m 0700 "$OUTPUT_DIR"
chmod 0700 "$OUTPUT_DIR"
enrollment_json="${OUTPUT_DIR}/vault-${USERNAME}-totp.json"
enrollment_png="${OUTPUT_DIR}/vault-${USERNAME}-totp.png"
enrollment_uri="${OUTPUT_DIR}/vault-${USERNAME}-totp-uri.txt"

enforcement_json="$(vault read -format=json "identity/mfa/login-enforcement/${ENFORCEMENT_NAME}" 2>/dev/null || true)"
if [[ -n "$enforcement_json" ]]; then
  printf '%s' "$enforcement_json" | jq -e --arg method "$method_id" \
    --arg accessor "$userpass_accessor" \
    '.data.mfa_method_ids | index($method)' >/dev/null || {
      echo "Existing MFA enforcement differs; refusing to replace it" >&2
      exit 1
    }
  printf '%s' "$enforcement_json" | jq -e --arg accessor "$userpass_accessor" \
    '.data.auth_method_accessors | index($accessor)' >/dev/null || {
      echo "Existing MFA enforcement belongs to another auth mount" >&2
      exit 1
    }
  echo "MFA enforcement already exists; TOTP secret was not regenerated."
else
  for path in "$enrollment_json" "$enrollment_png" "$enrollment_uri"; do
    [[ ! -e "$path" && ! -L "$path" ]] || {
      echo "Enrollment output exists; refusing to overwrite $path" >&2
      exit 1
    }
  done
  vault write -format=json identity/mfa/method/totp/admin-generate \
    method_id="$method_id" \
    entity_id="$entity_id" >"$enrollment_json"
  jq -r '.data.barcode' "$enrollment_json" | b64decode >"$enrollment_png"
  jq -r '.data.url' "$enrollment_json" >"$enrollment_uri"
  chmod 0600 "$enrollment_json" "$enrollment_png" "$enrollment_uri"
  vault write "identity/mfa/login-enforcement/${ENFORCEMENT_NAME}" \
    mfa_method_ids="$method_id" \
    auth_method_accessors="$userpass_accessor" >/dev/null
fi


cat <<EOF
vault_addr=$VAULT_ADDR
username=$USERNAME
policy=$POLICY_NAME
method_id=$method_id
userpass_accessor=$userpass_accessor
entity_id=$entity_id
ui_url=$UI_URL
EOF
if [[ -f "$enrollment_png" && -f "$enrollment_uri" ]]; then
  printf 'enrollment_png=%s\nenrollment_uri=%s\n' "$enrollment_png" "$enrollment_uri"
fi
