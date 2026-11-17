#!/usr/bin/env bash
set -euo pipefail

action="${1:-install}"
package_spec="${2:-}"

if [ -z "${package_spec}" ]; then
  echo "Usage: $0 <install|reinstall|upgrade|backup|restore|migrate> <npm-package-spec>" >&2
  exit 2
fi

package_name() {
  local spec="$1"
  if [[ "${spec}" == @* ]]; then
    local rest="${spec#@}"
    local scope="${rest%%/*}"
    local after_scope="${rest#*/}"
    local name="${after_scope%%@*}"
    printf '@%s/%s\n' "${scope}" "${name}"
  else
    printf '%s\n' "${spec%%@*}"
  fi
}

desired_version() {
  local spec="$1"
  if [[ "${spec}" == @* ]]; then
    local rest="${spec#@}"
    local after_scope="${rest#*/}"
    if [[ "${after_scope}" == *"@"* ]]; then
      printf '%s\n' "${after_scope#*@}"
    fi
  elif [[ "${spec}" == *"@"* ]]; then
    printf '%s\n' "${spec#*@}"
  fi
}

installed_version() {
  local name="$1"
  local npm_root
  npm_root="$(npm root -g)"
  node -e '
const fs = require("fs");
const path = require("path");
const pkg = process.argv[1];
const root = process.argv[2];
const packageJson = path.join(root, ...pkg.split("/"), "package.json");
if (!fs.existsSync(packageJson)) process.exit(1);
const parsed = JSON.parse(fs.readFileSync(packageJson, "utf8"));
process.stdout.write(parsed.version || "");
' "${name}" "${npm_root}"
}

is_installed() {
  local name="$1"
  local want="${2:-}"
  local have
  have="$(installed_version "${name}" 2>/dev/null || true)"
  [ -n "${have}" ] || return 1
  [ -z "${want}" ] || [ "${have}" = "${want}" ]
}

install_package() {
  local spec="$1"
  local name want
  name="$(package_name "${spec}")"
  want="$(desired_version "${spec}")"

  if is_installed "${name}" "${want}"; then
    echo "changed=0 action=install package=${spec}"
    return
  fi

  npm install -g --force "${spec}"
  echo "changed=1 action=install package=${spec}"
}

reinstall_package() {
  local spec="$1"
  npm install -g --force "${spec}"
  echo "changed=1 action=reinstall package=${spec}"
}

upgrade_package() {
  local spec="$1"
  npm install -g --force "${spec}"
  echo "changed=1 action=upgrade package=${spec}"
}

backup_package() {
  echo "changed=0 action=backup package=${1} status=reserved"
}

restore_package() {
  echo "changed=0 action=restore package=${1} status=reserved"
}

migrate_package() {
  echo "changed=0 action=migrate package=${1} status=reserved"
}

case "${action}" in
  install) install_package "${package_spec}" ;;
  reinstall) reinstall_package "${package_spec}" ;;
  upgrade) upgrade_package "${package_spec}" ;;
  backup) backup_package "${package_spec}" ;;
  restore) restore_package "${package_spec}" ;;
  migrate) migrate_package "${package_spec}" ;;
  *)
    echo "Unsupported npm package action: ${action}" >&2
    exit 2
    ;;
esac
