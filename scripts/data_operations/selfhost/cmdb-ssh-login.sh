# Resolve the exact environment-owned Web SaaS host and its safe SSH identity.
# Sets host_ip, ssh_user, and sudo_prefix (an array, never an interpolated user).
selfhost_cmdb_ssh_login() {
  local cmdb_file="$1" target="$2" environment="$3" record_env groups
  if [[ "$target" != "web-saas-${environment}" ]]; then
    echo "::error::target_host must be exactly web-saas-${environment}." >&2
    return 2
  fi
  if [[ ! -f "$cmdb_file" ]]; then
    echo "::error::CMDB artifact is missing." >&2
    return 2
  fi
  # The accepted scoped contract is the GCP CMDB shape: top-level environment
  # identity plus a flat per-host record. Other provider adapters fail closed.
  record_env="$(jq -r '.environment // empty' "$cmdb_file")"
  if [[ "$record_env" != "$environment" ]]; then
    echo "::error::CMDB environment identity is missing or does not match the requested environment." >&2
    return 2
  fi
  if ! jq -e --arg host "$target" '.[$host] | type == "object"' "$cmdb_file" >/dev/null; then
    echo "::error::CMDB has no record for the exact environment host." >&2
    return 2
  fi
  host_ip="$(jq -r --arg host "$target" '.[$host].ip // empty' "$cmdb_file")"
  if [[ ! "$host_ip" =~ ^[A-Fa-f0-9:.]+$ ]]; then
    echo "::error::CMDB has no valid IP for the exact Web SaaS host." >&2
    return 2
  fi
  groups="$(jq -r --arg host "$target" '(.[$host].groups // []) | join(",")' "$cmdb_file")"
  if [[ ",${groups}," != *,web_saas,* ]]; then
    echo "::error::CMDB target is not in the web_saas group." >&2
    return 2
  fi
  ssh_user="$(jq -r --arg host "$target" '.[$host].ansible_user // "root"' "$cmdb_file")"
  if [[ ! "$ssh_user" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]]; then
    echo "::error::Invalid SSH user in CMDB for the selected target." >&2
    return 2
  fi
  sudo_prefix=()
  if [[ "$ssh_user" != root ]]; then
    sudo_prefix=(sudo -n)
  fi
}
