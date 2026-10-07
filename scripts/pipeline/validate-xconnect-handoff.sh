#!/usr/bin/env bash
set -euo pipefail
# Dynamic addresses come exclusively from the same lab run's public IaC
# handoff. No permanent host/IP/user literals are selected by this validator.
jq -e '
  def ipv4: type == "string" and test("^[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+$") and
    (split(".") | length == 4 and all(.[]; (tonumber >= 0 and tonumber <= 255))) and
    . == (split(".") | map(tonumber | tostring) | join("."));
  def private_ipv4: ipv4 and (split(".") | map(tonumber) |
    .[0] == 10 or .[0] == 127 or (.[0] == 172 and .[1] >= 16 and .[1] <= 31) or
    (.[0] == 192 and .[1] == 168) or (.[0] == 169 and .[1] == 254));
  . as $handoff |
  keys == (["run","expires_at","network_id","gateway_id","gateway_public_key","gateway_endpoint",
    "accounts_url","portal_url","instances","expected_device_ids","verification"] | sort) and
  (.run | type == "string" and test("^xcl-[0-9]+-[0-9]+$")) and
  (.expires_at | fromdateiso8601 | type == "number") and
  (.network_id | test("^net_[a-z0-9][a-z0-9_-]*-" + $handoff.run + "$")) and
  .gateway_id == ("gw-" + .run) and (.gateway_public_key | test("^[A-Za-z0-9+/]{43}=$")) and
  .accounts_url == "https://accounts-uat.onwalk.net" and
  .portal_url == "https://console-serverless-uat.onwalk.net/panel/xconnect-zero" and
  (.gateway_endpoint | keys == ["host","port","server_name"] and (.host | ipv4) and .port == 443 and .server_name == "xconnect-lab.invalid") and
  (.instances | keys == ["gateway","linux_one"] and all(.[];
    keys == ["instance_id","private_ip","public_ip"] and (.instance_id | test("^i-[0-9a-f]+$")) and
    (.public_ip | ipv4) and (.private_ip | private_ipv4))) and
  (.gateway_endpoint.host == .instances.gateway.public_ip or .gateway_endpoint.host == .instances.gateway.private_ip) and
  .expected_device_ids == {darwin:("one-darwin-" + .run),windows:("one-windows-" + .run)} and
  (.verification | keys == ["expected_marker","target"] and .expected_marker == $handoff.run and
    (.target | capture("^http://(?<ip>[0-9.]+):8080/$").ip | private_ipv4))
' "${1:?Public handoff is required}" >/dev/null
