#!/usr/bin/env bash
# Take a Vault Raft snapshot through the API, verify it, prove it restores,
# encrypt it with an age public key, upload only the ciphertext to
# S3-compatible storage, and read the off-site copy back.
#
# Restore drill: the snapshot is force-restored into a disposable Raft Vault
# on the runner (its own throwaway init, one key share). Vault accepts the
# data and the disposable node comes back sealed under the restored barrier,
# i.e. it now waits for the production unseal key - which CI never holds. A
# full drill (decrypt with the offline age identity, restore, unseal, read a
# canary) stays with the operator; see the migration runbook.
#
# Environment:
#   VAULT_ADDR, VAULT_TOKEN      token from the snapshot-only JWT role
#   VAULT_DRILL_BIN              Vault binary for the disposable restore drill
#   BACKUP_AGE_RECIPIENT         age public key (the private key stays offline)
#   BACKUP_DESTINATION           s3://bucket/prefix
#   BACKUP_ENDPOINT              optional S3 endpoint URL
#   AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION
#
# The snapshot is already encrypted by Vault's barrier, but it is still
# handled as sensitive: the plaintext file lives only in a private runner
# directory and is removed on exit.
set -euo pipefail
umask 077

for name in VAULT_ADDR VAULT_TOKEN VAULT_DRILL_BIN BACKUP_AGE_RECIPIENT BACKUP_DESTINATION AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY; do
  [[ -n "${!name:-}" ]] || { echo "::error::${name} is required for vault-snapshot" >&2; exit 1; }
done
[[ "${BACKUP_AGE_RECIPIENT}" =~ ^age1[0-9a-z]{58}$ ]] || { echo "::error::BACKUP_AGE_RECIPIENT is not an age public key" >&2; exit 1; }
[[ "${BACKUP_DESTINATION}" =~ ^s3://[a-z0-9.-]+(/[A-Za-z0-9._/-]*)?$ ]] || { echo "::error::BACKUP_DESTINATION must be s3://bucket/prefix" >&2; exit 1; }
for command_name in curl tar sha256sum age aws jq; do
  command -v "${command_name}" >/dev/null 2>&1 || { echo "::error::${command_name} is required" >&2; exit 1; }
done
[[ -x "${VAULT_DRILL_BIN}" ]] || { echo "::error::VAULT_DRILL_BIN is not an executable Vault binary" >&2; exit 1; }

work="$(mktemp -d "${RUNNER_TEMP:-/tmp}/vault-snapshot.XXXXXX")"
drill_pid=""
cleanup() {
  [[ -z "${drill_pid}" ]] || kill "${drill_pid}" 2>/dev/null || true
  rm -rf -- "${work}"
}
trap cleanup EXIT

# drill SNAPSHOT: force-restore into a disposable single-node Raft Vault.
drill() {
  local snapshot="$1" dir="${work}/drill" port=18200 api init key token code
  mkdir -p "${dir}/raft"
  api="http://127.0.0.1:${port}"
  cat >"${dir}/vault.hcl" <<HCL
storage "raft" {
  path    = "${dir}/raft"
  node_id = "restore-drill"
}
listener "tcp" {
  address         = "127.0.0.1:${port}"
  cluster_address = "127.0.0.1:$((port + 1))"
  tls_disable     = true
}
api_addr      = "${api}"
cluster_addr  = "http://127.0.0.1:$((port + 1))"
disable_mlock = true
ui            = false
HCL
  env -u VAULT_TOKEN -u VAULT_ADDR "${VAULT_DRILL_BIN}" server -config="${dir}/vault.hcl" >"${dir}/server.log" 2>&1 &
  drill_pid=$!
  for _ in $(seq 1 30); do
    curl --silent --max-time 2 -o /dev/null "${api}/v1/sys/seal-status" && break
    sleep 1
  done
  init="$(curl --fail --silent --show-error -X PUT --data '{"secret_shares":1,"secret_threshold":1}' "${api}/v1/sys/init")"
  key="$(jq -er '.keys_base64[0]' <<<"${init}")"
  token="$(jq -er '.root_token' <<<"${init}")"
  jq -n --arg key "${key}" '{key:$key}' | curl --fail --silent --show-error -X PUT --data @- "${api}/v1/sys/unseal" >/dev/null
  for _ in $(seq 1 30); do
    [[ "$(curl --silent --max-time 2 -o /dev/null -w '%{http_code}' "${api}/v1/sys/health")" == 200 ]] && break
    sleep 1
  done
  code="$(curl --silent --show-error -o "${dir}/restore.out" -w '%{http_code}' -X POST \
    -H "X-Vault-Token: ${token}" --data-binary @"${snapshot}" "${api}/v1/sys/storage/raft/snapshot-force")"
  [[ "${code}" == 204 ]] || { echo "::error::restore drill: Vault rejected the snapshot (HTTP ${code})" >&2; return 1; }
  # The restored data carries the production barrier, so the disposable
  # node must now report itself sealed (waiting for the production key).
  for _ in $(seq 1 30); do
    if curl --silent --max-time 2 "${api}/v1/sys/seal-status" | jq -e '.initialized == true and .sealed == true' >/dev/null; then
      kill "${drill_pid}" 2>/dev/null || true
      wait "${drill_pid}" 2>/dev/null || true
      drill_pid=""
      rm -rf -- "${dir}"
      return 0
    fi
    sleep 1
  done
  echo "::error::restore drill: the disposable Vault did not switch to the restored barrier" >&2
  return 1
}
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
snapshot="${work}/vault-${stamp}.snap"

curl --fail --silent --show-error --max-time 600 \
  -H "X-Vault-Token: ${VAULT_TOKEN}" \
  -o "${snapshot}" "${VAULT_ADDR%/}/v1/sys/storage/raft/snapshot"
[[ -s "${snapshot}" ]] || { echo "::error::Vault returned an empty snapshot" >&2; exit 1; }

# A Raft snapshot is a gzip tar with meta.json, state.bin and SHA256SUMS.
mkdir "${work}/check"
tar -xzf "${snapshot}" -C "${work}/check"
for member in meta.json state.bin SHA256SUMS; do
  [[ -s "${work}/check/${member}" ]] || { echo "::error::snapshot is missing ${member}" >&2; exit 1; }
done
(cd "${work}/check" && sha256sum --check --quiet SHA256SUMS) || {
  echo '::error::snapshot checksums do not match' >&2
  exit 1
}
index="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("Index", "?"))' "${work}/check/meta.json")"
rm -rf "${work}/check"
drill "${snapshot}"
echo "Restore drill passed: the snapshot restored into a disposable Raft Vault"

encrypted="${snapshot}.age"
age --encrypt --recipient "${BACKUP_AGE_RECIPIENT}" --output "${encrypted}" "${snapshot}"
rm -f -- "${snapshot}"
digest="$(sha256sum "${encrypted}" | cut -d' ' -f1)"
size="$(du -h "${encrypted}" | cut -f1)"

endpoint_args=()
[[ -n "${BACKUP_ENDPOINT:-}" ]] && endpoint_args=(--endpoint-url "${BACKUP_ENDPOINT}")
target="${BACKUP_DESTINATION%/}/vault-${stamp}.snap.age"
aws "${endpoint_args[@]}" s3 cp --only-show-errors "${encrypted}" "${target}"
printf '%s  vault-%s.snap.age\n' "${digest}" "${stamp}" >"${work}/sha256"
aws "${endpoint_args[@]}" s3 cp --only-show-errors "${work}/sha256" "${target}.sha256"

# Read the off-site copy back: what is stored is what was encrypted.
aws "${endpoint_args[@]}" s3 cp --only-show-errors "${target}" "${work}/readback.age"
[[ "$(sha256sum "${work}/readback.age" | cut -d' ' -f1)" == "${digest}" ]] || {
  echo "::error::the off-site snapshot does not read back intact" >&2
  exit 1
}
jq -n --arg index "${index}" --arg sha256 "${digest}" --arg taken "${stamp}" --arg version "$("${VAULT_DRILL_BIN}" version | awk '{print $2}')" \
  '{raft_index:$index, sha256:$sha256, taken_at:$taken, restore_drill:"passed", drill_vault:$version, readback:"verified"}' \
  >"${work}/manifest.json"
aws "${endpoint_args[@]}" s3 cp --only-show-errors "${work}/manifest.json" "${target}.json"

report="Vault snapshot at Raft index ${index}: ${target} (${size}, sha256 ${digest}; restore drill passed, off-site copy verified)"
echo "${report}"
if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  echo "- ${report}" >>"${GITHUB_STEP_SUMMARY}"
fi
