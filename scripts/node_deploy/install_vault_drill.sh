#!/usr/bin/env bash
# Install the Vault binary used for the snapshot restore drill on the runner.
#
#   install_vault_drill.sh VERSION DEST_DIR
#
# Downloads the official linux_amd64 release and checks it against the
# release's SHA256SUMS; anything missing or mismatched fails closed.
set -euo pipefail
umask 077

version="$1" dest="$2"
[[ "${version}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "::error::Vault drill version must be X.Y.Z" >&2; exit 1; }
work="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/vault-drill-download.XXXXXX")"
trap 'rm -rf -- "${work}"' EXIT
base="https://releases.hashicorp.com/vault/${version}"
asset="vault_${version}_linux_amd64.zip"

curl --fail --silent --show-error --location --max-time 120 -o "${work}/${asset}" "${base}/${asset}"
curl --fail --silent --show-error --location --max-time 60 -o "${work}/SHA256SUMS" "${base}/vault_${version}_SHA256SUMS"
awk -v asset="${asset}" '$2 == asset' "${work}/SHA256SUMS" >"${work}/SHA256SUMS.selected"
[[ -s "${work}/SHA256SUMS.selected" ]] || { echo "::error::no checksum for ${asset}" >&2; exit 1; }
(cd "${work}" && sha256sum --check --quiet SHA256SUMS.selected) || { echo "::error::Vault ${version} checksum mismatch" >&2; exit 1; }

mkdir -p "${dest}"
unzip -o -q "${work}/${asset}" vault -d "${dest}"
chmod 755 "${dest}/vault"
"${dest}/vault" version
