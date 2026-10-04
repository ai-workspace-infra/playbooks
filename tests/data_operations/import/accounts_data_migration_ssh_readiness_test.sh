#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
script="${repo_root}/scripts/data_operations/accounts_data_migration_ssh.sh"

grep -Fq 'SSH_READY_ATTEMPTS="${SSH_READY_ATTEMPTS:-60}"' "${script}"
grep -Fq 'SSH_READY_INTERVAL_SECONDS="${SSH_READY_INTERVAL_SECONDS:-3}"' "${script}"
grep -Fq 'SSH_AUTH_ATTEMPTS="${SSH_AUTH_ATTEMPTS:-5}"' "${script}"
grep -Fq 'preflight_endpoint source "${SOURCE_HOST}" "${SOURCE_ADDR}"' "${script}"
grep -Fq 'preflight_endpoint target "${TARGET_HOST}" "${TARGET_ADDR}"' "${script}"
grep -Fq 'ConnectTimeout=${SSH_READY_CONNECT_TIMEOUT_SECONDS}' "${script}"
grep -Fq 'SSH_KEY_PATH="${MIGRATION_SSH_KEY_PATH:-${HOME}/.ssh/id_deploy}"' "${script}"
grep -Fq -- '-i "${SSH_KEY_PATH}"' "${script}"

bash -n "${script}"
echo "accounts_data_migration_ssh_readiness_test: PASS"
