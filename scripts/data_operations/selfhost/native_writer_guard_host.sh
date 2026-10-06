#!/usr/bin/env bash
# Read-only target check. Never stop/start services or silently change restart policies.
set -euo pipefail
command -v docker >/dev/null || exit 0
names="$(docker ps -a --format '{{.Names}}')"
while IFS= read -r name; do
  [[ -n "$name" ]] || continue
  if [[ "$name" == web-saas-* && "$name" != web-saas-postgresql || "$name" == *doco-cd* || "$name" == *doco_cd* ]]; then
    state="$(docker inspect -f '{{.State.Running}}:{{.HostConfig.RestartPolicy.Name}}' "$name")"
    [[ "$state" == false:no ]] || {
      echo 'Native standby requires stopped application/reconciler containers with restart disabled.' >&2
      exit 1
    }
  fi
done <<< "$names"
echo 'Native standby application and reconciler guard passed.'
