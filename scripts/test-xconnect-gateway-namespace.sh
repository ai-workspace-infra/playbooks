#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "namespace apply smoke test safely skipped outside Linux"
  exit 0
fi
for command in unshare nft ip; do
  command -v "${command}" >/dev/null || { echo "namespace apply smoke test skipped: missing ${command}"; exit 0; }
done

# The child receives a fresh user+network namespace. Nothing below can address
# the runner's host links or nftables ruleset.
unshare --user --map-root-user --net --mount-proc bash -euo pipefail <<'NS'
ip link set lo up
ip link add wg-xco type dummy
ip addr add 10.77.0.1/32 dev wg-xco
ip link set wg-xco up
nft --check -f - <<'NFT'
table inet xconnect_one {
  chain forward { type filter hook forward priority filter; policy accept;
    iifname "wg-xco" jump overlay
    oifname "wg-xco" jump overlay
  }
  chain overlay {
    ip saddr { 10.77.0.10/32 } ip daddr { 10.77.0.11/32 } tcp dport { 8787 } accept comment "allow-api"
    ip saddr { 10.77.0.11/32 } ip daddr { 10.77.0.10/32 } tcp sport { 8787 } ct state established,related accept comment "allow-api-return"
    drop
  }
}
NFT
nft -f - <<'NFT'
table inet xconnect_one {
  chain forward { type filter hook forward priority filter; policy accept;
    iifname "wg-xco" jump overlay
    oifname "wg-xco" jump overlay
  }
  chain overlay { drop }
}
NFT
nft list table inet xconnect_one >/dev/null
# Local controller traffic is output/input, not overlay forward; loopback stays up.
ip route get 127.0.0.1 | grep -q 'dev lo'
NS

echo "isolated namespace apply smoke test passed"
