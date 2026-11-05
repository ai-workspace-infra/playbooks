#!/usr/bin/env bash
set -Eeuo pipefail

while IFS= read -r device; do
    [[ "$device" == lo ]] && continue
    tc qdisc replace dev "$device" root fq
done < <(ip -o link show | awk -F': ' '{print $2}' | cut -d'@' -f1)
