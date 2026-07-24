#!/usr/bin/env bash
# 读取可选的 GITOPS_TOKEN, 缺失即视为空, 导出到 GITHUB_ENV。
#
# 为什么不用 hashicorp/vault-action 直接读: kv/data/CICD/<env> 这个路径本身
# 存在(装着 VULTR_API_KEY / TF_STATE_* / SSH_PRIVATE_DEPLOY_KEY_B64), 只是
# 缺 GITOPS_TOKEN 这个键。vault-action 的 ignoreNotFound 只在整个路径 404
# 时生效, 路径存在、单个 selector(键)缺失照样以 "No match data was found"
# 硬失败 —— 和 platform-ops-toolkit 那两次"存在但缺键"是同一类问题。
#
# gitops 仓是公开的, 读取不需要凭据; GITOPS_TOKEN 只用于 CD 侧的**写入**
# (推送新的镜像 tag)。它是否存在由发布脚本(domain-cd-publish-tag.sh)
# 自己判断是否致命 —— 这里只负责"缺就给空", 不在这一步下结论。
#
# 认证走 GitHub OIDC -> Vault JWT role, 与其余步骤一致, 不引入静态 token
# 依赖。
set -euo pipefail

: "${VAULT_ADDR:?VAULT_ADDR is required}"
: "${VAULT_ROLE:?VAULT_ROLE is required}"
: "${VAULT_KV_BASE:?VAULT_KV_BASE is required (e.g. kv/data/CICD/uat)}"
: "${GITHUB_ENV:?GITHUB_ENV is required}"
: "${ACTIONS_ID_TOKEN_REQUEST_URL:?ACTIONS_ID_TOKEN_REQUEST_URL is required (needs id-token: write)}"
: "${ACTIONS_ID_TOKEN_REQUEST_TOKEN:?ACTIONS_ID_TOKEN_REQUEST_TOKEN is required (needs id-token: write)}"

oidc="$(curl -sS --retry 3 \
  -H "Authorization: bearer ${ACTIONS_ID_TOKEN_REQUEST_TOKEN}" \
  "${ACTIONS_ID_TOKEN_REQUEST_URL}&audience=vault")"
gh_jwt="$(jq -r '.value // empty' <<<"${oidc}")"
[[ -n "${gh_jwt}" ]] || {
  echo "::error::Failed to obtain a GitHub OIDC token for audience=vault." >&2
  exit 1
}

login_body="$(mktemp)"
trap 'rm -f "${login_body}"' EXIT
login_status="$(curl -sS -o "${login_body}" -w '%{http_code}' \
  -X POST -H "Content-Type: application/json" \
  -d "$(jq -n --arg role "${VAULT_ROLE}" --arg jwt "${gh_jwt}" '{role: $role, jwt: $jwt}')" \
  "${VAULT_ADDR}/v1/auth/jwt/login")"
[[ "${login_status}" == "200" ]] || {
  echo "::error::Vault JWT login failed (HTTP ${login_status}) for role ${VAULT_ROLE}." >&2
  head -c 300 "${login_body}" >&2
  exit 1
}
vault_token="$(jq -r '.auth.client_token // empty' < "${login_body}")"
[[ -n "${vault_token}" ]] || {
  echo "::error::Vault JWT login response had no client_token." >&2
  exit 1
}

body="$(mktemp)"
status="$(curl -sS -o "${body}" -w '%{http_code}' \
  -H "X-Vault-Token: ${vault_token}" "${VAULT_ADDR}/v1/${VAULT_KV_BASE}")"

if [[ "${status}" != "200" && "${status}" != "404" ]]; then
  echo "::error::Reading ${VAULT_KV_BASE} returned HTTP ${status}." >&2
  head -c 300 "${body}" >&2
  rm -f "${body}"
  exit 1
fi

if [[ "${status}" == "200" ]]; then
  token="$(jq -r '.data.data.GITOPS_TOKEN // ""' < "${body}")"
else
  token=""
fi
rm -f "${body}"

[[ -n "${token}" ]] && echo "::add-mask::${token}"
echo "GITOPS_TOKEN=${token}" >> "${GITHUB_ENV}"
echo "Loaded GITOPS_TOKEN from ${VAULT_KV_BASE} (missing -> empty)."
