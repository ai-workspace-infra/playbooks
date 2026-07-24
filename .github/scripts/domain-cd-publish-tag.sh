#!/usr/bin/env bash
# 把 deploy_tag 写进 gitops 仓的 compose/<domain>/.env.<env>, 提交并推送。
# Doco-CD 在目标主机上轮询该仓库(60s), 拉到新 commit 即部署。
#
# 这一步就是"部署"本身: 版本轴是 git ref, 提交历史即部署历史。CD 不在
# 部署时决定版本 —— deploy_tag 由调用方显式给出。
set -euo pipefail

: "${DOMAIN:?DOMAIN is required}"
: "${DEPLOY_ENV:?DEPLOY_ENV is required}"
: "${DEPLOY_TAG:?DEPLOY_TAG is required}"
: "${MANAGED_IMAGES:?MANAGED_IMAGES is required (space-separated env var names)}"
: "${GITOPS_REPO:?GITOPS_REPO is required}"

workdir="$(mktemp -d)"
trap 'rm -rf "${workdir}"' EXIT

git clone --depth 1 --quiet "https://x-access-token:${GITOPS_TOKEN:?GITOPS_TOKEN is required}@github.com/${GITOPS_REPO}.git" "${workdir}/gitops"
cd "${workdir}/gitops"

env_file="compose/${DOMAIN}/.env.${DEPLOY_ENV}"
# 文件不存在就必须失败。sed 对不存在的文件会报错, 但 grep -c 在这里更早
# 也更明确 —— 静默创建一个新文件等于凭空发明一套部署声明。
if [ ! -f "${env_file}" ]; then
  echo "::error::${env_file} does not exist in ${GITOPS_REPO}. The compose stack for domain '${DOMAIN}' env '${DEPLOY_ENV}' has not been declared." >&2
  exit 1
fi

changed=0
for var in ${MANAGED_IMAGES}; do
  # 只替换 tag 部分, 保留 registry/repo —— 各服务镜像仓库不同, 整行覆盖
  # 会把 repo 也一并写错。
  if ! grep -qE "^${var}=" "${env_file}"; then
    echo "::error::${var} is not declared in ${env_file}; refusing to invent it." >&2
    exit 1
  fi
  before="$(grep -E "^${var}=" "${env_file}")"
  repo="${before#*=}"
  repo="${repo%:*}"
  after="${var}=${repo}:${DEPLOY_TAG}"
  if [ "${before}" != "${after}" ]; then
    # 用 awk 而非 sed -i: BSD 与 GNU sed 的 -i 语义不同, 而 runner 与本地
    # 开发机未必是同一个。
    awk -v v="${var}=" -v new="${after}" '
      index($0, v) == 1 { print new; next } { print }
    ' "${env_file}" > "${env_file}.tmp"
    mv "${env_file}.tmp" "${env_file}"
    echo "  ${before}  ->  ${after}"
    changed=1
  else
    echo "  ${before}  (unchanged)"
  fi
done

if [ "${changed}" -eq 0 ]; then
  echo "No image tag changed; nothing to publish. Doco-CD already has this version."
  exit 0
fi

git config user.name "ai-workspace-infra-cd"
git config user.email "cd@ai-workspace-infra.noreply.github.com"
git add "${env_file}"
git commit --quiet -m "deploy(${DOMAIN}/${DEPLOY_ENV}): ${DEPLOY_TAG}

Published by ${GITHUB_REPOSITORY:-unknown}@${GITHUB_RUN_ID:-unknown}.
Doco-CD polls this repository and applies the change on the target host."
git push --quiet origin HEAD:main

echo "Published ${DEPLOY_TAG} to ${GITOPS_REPO}/${env_file}"
echo "Doco-CD polls every 60s; the target host will converge within that window."
