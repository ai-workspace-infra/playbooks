#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/desktop-public"
run=xcl-123-2
fixture() {
  printf '%s' "$run" > "$tmp/run-id"
  jq -n --arg run "$run" '{run_id:$run,expires_at:"2099-01-01T00:00:00Z"}' > "$tmp/variables.json"
  jq -n --arg run "$run" '{gateway_provider:{value:"aws-spot"},gateway_role:{value:"relay"},client_role:{value:"controlled-client"},
    gateway_ssh_user:{value:"gateway-user"},client_ssh_user:{value:"one-user"},
    resource_ids:{value:{run:$run,gateway:"i-abc",client:"i-def",gateway_source:"aws-spot"}},
    gateway_ip:{value:"198.51.100.7"},gateway_private_ip:{value:"10.0.0.7"},gateway_transport_ip:{value:"198.51.100.7"},
    client_ip:{value:"203.0.113.9"},client_private_ip:{value:"10.0.0.9"}}' > "$tmp/outputs.json"
  jq -n --arg run "$run" '{run:$run,expires_at:"2099-01-01T00:00:00Z",network_id:("net_uat-"+$run),gateway_id:("gw-"+$run),
    gateway_public_key:"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
    gateway_endpoint:{host:"198.51.100.7",port:443,server_name:"xconnect-lab.invalid"},
    accounts_url:"https://accounts-uat.onwalk.net",portal_url:"https://console-serverless-uat.onwalk.net/panel/xconnect-zero",
    instances:{gateway:{instance_id:"i-abc",public_ip:"198.51.100.7",private_ip:"10.0.0.7"},linux_one:{instance_id:"i-def",public_ip:"203.0.113.9",private_ip:"10.0.0.9"}},
    expected_device_ids:{darwin:("one-darwin-"+$run),windows:("one-windows-"+$run)},
    verification:{expected_marker:$run,target:"http://10.0.0.9:8080/"}}' > "$tmp/desktop-public/desktop-handoff.json"
}
mutate() { jq "$2" "$tmp/$1" > "$tmp/new"; mv "$tmp/new" "$tmp/$1"; }
pass() { bash "$root/validate-xconnect-targets.sh" "$tmp" "$run"; echo "PASS $1"; }
reject() { if bash "$root/validate-xconnect-targets.sh" "$tmp" "$run" >/dev/null 2>&1; then echo "Unexpected acceptance: $1" >&2; exit 1; fi; echo "PASS reject $1"; }
fixture; pass 'dynamic host and user from IaC'
fixture; mutate outputs.json '.resource_ids.value.run="xcl-122-2"'; reject 'different IaC run'
fixture; mutate variables.json '.run_id="xcl-123-1"'; reject 'different attempt'
fixture; mutate outputs.json '.resource_ids.value.gateway="i-aaa"'; reject 'different resource identity'
fixture; mutate outputs.json '.client_ip.value="203.0.113.10"'; reject 'different One address'
fixture; mutate outputs.json '.gateway_ssh_user.value="root -o ProxyCommand=x"'; reject 'unsafe SSH selector'
fixture; mutate desktop-public/desktop-handoff.json '.expires_at="2099-01-02T00:00:00Z"'; reject 'different lease'
fixture; mutate desktop-public/desktop-handoff.json '.token="forbidden"'; reject 'unexpected credential field'
fixture; mutate outputs.json '.gateway_provider.value="external" | .resource_ids.value.gateway_source="external" | .resource_ids.value.gateway="external"'; rm "$tmp/desktop-public/desktop-handoff.json"; pass 'external dynamic provider branch'
echo '9 dynamic target checks passed; no host connection performed.'
