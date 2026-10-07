#!/usr/bin/env bash
# Download and verify the reviewed XConnect runtime for one CPU architecture.
#
#   xconnect_artifacts.sh OUT_DIR ARCH COMPONENTS
#
# ARCH is amd64 or arm64; COMPONENTS is a comma list of gateway and/or one.
# Release tags come from the GitOps topology (runtime.gateway_release,
# runtime.cli_release, runtime.xray_release) through GATEWAY_RELEASE_TAG,
# ONE_RELEASE_TAG and XRAY_RELEASE_TAG. Every file is checked against the
# release's published checksum; anything missing or mismatched fails closed.
# RELEASE_TOKEN reads the private ai-workspace-xstream releases; GITHUB_TOKEN
# reads the public Xray release.
set -euo pipefail
umask 077

out="$1" arch="$2" components=",$3,"
die() { echo "::error::$*" >&2; exit 1; }

case "${arch}" in
  amd64) xray_asset='Xray-linux-64.zip' ;;
  arm64) xray_asset='Xray-linux-arm64-v8a.zip' ;;
  *) die "unsupported XConnect architecture ${arch}" ;;
esac
tag_pattern='^v[0-9]+\.[0-9]+\.[0-9]+$'
work="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/xconnect-releases.XXXXXX")"
trap 'rm -rf -- "${work}"' EXIT
mkdir -p "${out}"

# fetch REPO TAG ASSET DEST: download ASSET and its SHA256SUMS line, verify, install.
fetch() {
  local repo="$1" tag="$2" asset="$3" dest="$4" dir="${work}/${3}"
  [[ "${tag}" =~ ${tag_pattern} ]] || die "release tag for ${asset} must be vX.Y.Z (got '${tag}')"
  [[ -n "${RELEASE_TOKEN:-}" ]] || die "RELEASE_TOKEN is required for the private ${repo} release"
  mkdir -p "${dir}"
  GH_TOKEN="${RELEASE_TOKEN}" gh release download "${tag}" --repo "${repo}" \
    --pattern "${asset}" --pattern SHA256SUMS --dir "${dir}" --clobber >/dev/null \
    || die "${repo} ${tag} download failed"
  awk -v asset="${asset}" '$2 == asset || $2 == "dist/" asset {sub("dist/", "", $2); print}' \
    "${dir}/SHA256SUMS" > "${dir}/SHA256SUMS.selected"
  [[ -s "${dir}/SHA256SUMS.selected" ]] || die "${repo} ${tag} has no checksum for ${asset}"
  (cd "${dir}" && sha256sum -c SHA256SUMS.selected >/dev/null) || die "${repo} ${tag} checksum mismatch for ${asset}"
  install -m 755 "${dir}/${asset}" "${out}/${dest}"
}

if [[ "${components}" == *",gateway,"* ]]; then
  fetch ai-workspace-xstream/XConnect-Gateway "${GATEWAY_RELEASE_TAG:-}" "xconnect-gateway-linux-${arch}" xconnect-gateway

  [[ "${XRAY_RELEASE_TAG:-}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "XRAY_RELEASE_TAG must be vX.Y.Z"
  GH_TOKEN="${GITHUB_TOKEN:-}" gh release download "${XRAY_RELEASE_TAG}" --repo XTLS/Xray-core \
    --pattern "${xray_asset}" --pattern "${xray_asset}.dgst" --dir "${work}" --clobber >/dev/null \
    || die "Xray ${XRAY_RELEASE_TAG} download failed"
  expected="$(awk '$1 == "SHA2-256=" {print $2; exit}' "${work}/${xray_asset}.dgst")"
  actual="$(sha256sum "${work}/${xray_asset}" | awk '{print $1}')"
  [[ "${expected}" =~ ^[0-9a-f]{64}$ && "${expected}" == "${actual}" ]] || die "Xray ${XRAY_RELEASE_TAG} checksum mismatch"
  unzip -p "${work}/${xray_asset}" xray > "${out}/xray" || die "Xray archive has no xray binary"
  chmod 755 "${out}/xray"
fi

if [[ "${components}" == *",one,"* ]]; then
  fetch ai-workspace-xstream/XConnect-One "${ONE_RELEASE_TAG:-}" "xconnect-linux-${arch}" xconnect
fi

ls -l "${out}"
