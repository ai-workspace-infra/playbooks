#!/usr/bin/env bash
set -euo pipefail

: "${DOMAIN:?DOMAIN is required}"
: "${DEPLOY_ENV:?DEPLOY_ENV is required}"
: "${TARGET_HOST:?TARGET_HOST is required}"

: "${GITOPS_REPO:=ai-workspace-infra/gitops}"
: "${GITOPS_BRANCH:=main}"
: "${GITOPS_ENV_PATH:=compose/${DOMAIN}/.env.${DEPLOY_ENV}}"

echo "Validated ${DOMAIN}/${DEPLOY_ENV} delivery request."
echo "CD is pull-only: Doco-CD on ${TARGET_HOST} polls public ${GITOPS_REPO}."
echo "No GitHub Actions step pushes GitOps changes."

if [[ -z "${DEPLOY_TAG:-}" ]]; then
  echo "No deploy_tag requested (infrastructure-only dispatch) — nothing to reconcile."
  exit 0
fi

echo "Requested deployment ref: ${DEPLOY_TAG}"

if [[ -z "${MANAGED_IMAGES:-}" ]]; then
  echo "::warning::${DOMAIN} has no managed_images wired to this reusable workflow yet — cannot verify '${DEPLOY_TAG}' is actually what gitops will deploy. Not a pass, just unverifiable."
  exit 0
fi

env_url="https://raw.githubusercontent.com/${GITOPS_REPO}/${GITOPS_BRANCH}/${GITOPS_ENV_PATH}"
env_file="$(mktemp)"
if ! curl -fsSL "${env_url}" -o "${env_file}"; then
  echo "::error::Could not fetch ${env_url} to verify the requested deploy_tag. Refusing to report success for a delivery we can't confirm." >&2
  exit 1
fi

mismatches=()
for var in ${MANAGED_IMAGES}; do
  line="$(grep -E "^${var}=" "${env_file}" || true)"
  if [[ -z "${line}" ]]; then
    mismatches+=("${var}: not set in ${GITOPS_ENV_PATH}")
    continue
  fi
  image_ref="${line#*=}"
  actual_tag="${image_ref##*:}"
  if [[ "${actual_tag}" != "${DEPLOY_TAG}" ]]; then
    mismatches+=("${var}: gitops pins '${actual_tag}', requested '${DEPLOY_TAG}'")
  fi
done

if [[ "${#mismatches[@]}" -gt 0 ]]; then
  echo "::error::Requested deploy_tag='${DEPLOY_TAG}' will NOT be applied — CD is pull-only and never writes deploy_tag into gitops for you. ${GITOPS_ENV_PATH} disagrees:" >&2
  for m in "${mismatches[@]}"; do
    echo "::error::  - ${m}" >&2
  done
  echo "::error::Update ${GITOPS_ENV_PATH} in ${GITOPS_REPO} directly (see docs/domains/IMAGE-TAG-CONTRACT.md), or re-dispatch with the tag that's actually pinned there." >&2
  exit 1
fi

echo "Confirmed: gitops ${GITOPS_ENV_PATH} already pins every managed image to '${DEPLOY_TAG}'."
