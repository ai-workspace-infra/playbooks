#!/usr/bin/env bash
set -euo pipefail

if [[ "${INPUT_RUN_INFRASTRUCTURE:-false}" == "true" ]]; then
  inventory_path="../cmdb/inventory.ini"
else
  inventory_path="../platform-ops-toolkit/inventory.ini"
fi

cd playbooks

# DNS now points accounts-<env> at the current Web SaaS host.  Remove the
# bootstrap-only /etc/hosts override so a future console replacement is not
# shadowed by a stale IP on a long-lived agent-proxy host.
# Keep SSH host-key handling in Ansible's environment instead of passing a
# value beginning with `-o` through argparse; older runner Ansible versions
# interpret that CLI value as a missing option argument.
max_attempts="${DNS_SSH_RETRIES:-10}"
for ((attempt = 1; attempt <= max_attempts; attempt++)); do
  if ANSIBLE_HOST_KEY_CHECKING=False ansible agent_proxy -i "${inventory_path}" \
    -m ansible.builtin.lineinfile \
    -a "path=/etc/hosts regexp='^[[:space:]]*[^#[:space:]]+[[:space:]]+[^#[:space:]]+[[:space:]]+# platform-ops temporary agent controller$' state=absent" \
    --private-key ~/.ssh/id_deploy \
    --become; then
    exit 0
  fi

  rc=$?
  if [[ "${rc}" -ne 4 || "${attempt}" -eq "${max_attempts}" ]]; then
    exit "${rc}"
  fi

  echo "SSH to one or more agent-proxy hosts is temporarily unavailable; retrying DNS cleanup (${attempt}/${max_attempts})..." >&2
  sleep 6
done
