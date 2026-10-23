#!/usr/bin/env bash
# Controller only: no SSH, Vault access, host bootstrap or provider operations.
set -euo pipefail
: "${ANSIBLE_COLLECTIONS_PATH:?explicit private controller collection directory required}"
case "$ANSIBLE_COLLECTIONS_PATH" in
  /*) ;;
  *) echo 'Controller collection directory must be absolute' >&2; exit 2 ;;
esac
case "$ANSIBLE_COLLECTIONS_PATH" in
  /|*:*) echo 'A single private controller collection directory is required' >&2; exit 2 ;;
esac
export ANSIBLE_COLLECTIONS_SCAN_SYS_PATH=False
role_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
umask 077
mkdir -p -- "$ANSIBLE_COLLECTIONS_PATH"
ansible-galaxy collection install -r "$role_dir/controller-collections.yml" -p "$ANSIBLE_COLLECTIONS_PATH"
