#!/usr/bin/env bash
set -euo pipefail

: "${TARGET_IP:?TARGET_IP is required}"
: "${COMPONENT:?COMPONENT is required}"
: "${CONTAINER:?CONTAINER is required}"
: "${PORT:?PORT is required}"
: "${SSH_PRIVATE_KEY_PATH:?SSH_PRIVATE_KEY_PATH is required}"

[[ "${TARGET_IP}" =~ ^[0-9.]+$ ]] || { echo "Target must be an IPv4 address." >&2; exit 2; }
[[ "${PORT}" =~ ^[0-9]+$ ]] || { echo "MCP port must be numeric." >&2; exit 2; }
[[ "${COMPONENT}" =~ ^(grafana|victoriametrics|victorialogs|victoriatraces)$ ]] || {
  echo "Unsupported MCP component." >&2; exit 2;
}

ssh_args=(-i "${SSH_PRIVATE_KEY_PATH}" -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=15)
echo "Verifying ${COMPONENT} MCP on the Observability target."
ssh "${ssh_args[@]}" "root@${TARGET_IP}" bash -s -- "${COMPONENT}" "${CONTAINER}" "${PORT}" <<'REMOTE'
set -euo pipefail
component="$1"
container="$2"
port="$3"

test "$(docker inspect --format '{{.State.Running}}' "${container}")" = true || {
  echo "${container} is not running." >&2; exit 1;
}

# Assert that the enabled MCP container received the backend URL belonging to
# this matrix component. Never print other environment entries because they
# may contain credentials.
case "${component}" in
  grafana) expected_backend='GRAFANA_URL=http://grafana:3000' ;;
  victoriametrics) expected_backend='VM_INSTANCE_ENTRYPOINT=http://victoria-metrics:8428' ;;
  victorialogs) expected_backend='VL_INSTANCE_ENTRYPOINT=http://victoria-logs:9428' ;;
  victoriatraces) expected_backend='VT_INSTANCE_ENTRYPOINT=http://victoria-traces:10428' ;;
esac
docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "${container}" \
  | grep --fixed-strings --line-regexp --quiet "${expected_backend}" || {
    echo "${component} MCP is not configured for its matching backend." >&2; exit 1;
  }
echo "${component} MCP backend association verified."

# Exercise the MCP Streamable HTTP initialize + tools/list flow, not just a TCP
# connection. Do not print headers, environment values, tokens, or tool inputs.
python3 - "${component}" "${port}" <<'PY'
import json
import sys
import urllib.error
import urllib.request

component, port = sys.argv[1], int(sys.argv[2])
url = f"http://127.0.0.1:{port}/mcp"
session_id = None
protocol_version = "2025-03-26"

def rpc(method, request_id, params=None):
    global session_id
    payload = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        payload["params"] = params
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if request_id != 1:
        headers["MCP-Protocol-Version"] = protocol_version
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode()
            session_id = response.headers.get("Mcp-Session-Id", session_id)
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"{component} MCP {method} returned HTTP {exc.code}") from None
    except Exception as exc:
        raise SystemExit(f"{component} MCP {method} failed: {type(exc).__name__}") from None

    documents = []
    try:
        documents.append(json.loads(raw))
    except json.JSONDecodeError:
        for line in raw.splitlines():
            if line.startswith("data:"):
                try:
                    documents.append(json.loads(line[5:].strip()))
                except json.JSONDecodeError:
                    pass
    for document in documents:
        if document.get("id") == request_id:
            if "error" in document:
                raise SystemExit(f"{component} MCP {method} returned a JSON-RPC error")
            if "result" in document:
                return document["result"]
    raise SystemExit(f"{component} MCP {method} returned no matching JSON-RPC result")

initialized = rpc("initialize", 1, {
    "protocolVersion": "2025-03-26",
    "capabilities": {},
    "clientInfo": {"name": "observability-deployment-verifier", "version": "1.0"},
})
protocol_version = initialized.get("protocolVersion", protocol_version)
notification = urllib.request.Request(
    url,
    data=json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode(),
    headers={
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": protocol_version,
        **({"Mcp-Session-Id": session_id} if session_id else {}),
    },
    method="POST",
)
try:
    with urllib.request.urlopen(notification, timeout=20):
        pass
except urllib.error.HTTPError as exc:
    if exc.code not in (202, 204):
        raise SystemExit(f"{component} MCP initialized notification returned HTTP {exc.code}") from None
except Exception as exc:
    raise SystemExit(f"{component} MCP initialized notification failed: {type(exc).__name__}") from None
tools = rpc("tools/list", 2).get("tools", [])
if not tools:
    raise SystemExit(f"{component} MCP returned no tools")
print(f"{component} MCP protocol ready; {len(tools)} tools advertised.")

if component == "grafana":
    # Confirm the service-account token is present without emitting its value,
    # then ask Grafana to health-check each provisioned Victoria datasource.
    env_text = open("/opt/observability-server/mcp-grafana.env", encoding="utf-8").read()
    token = next((line.split("=", 1)[1].strip() for line in env_text.splitlines()
                  if line.startswith("GRAFANA_SERVICE_ACCOUNT_TOKEN=")), "")
    if not token:
        raise SystemExit("Grafana MCP service-account token is missing")
    for uid in ("victoriametrics", "victorialogs", "victoriatraces"):
        request = urllib.request.Request(
            f"http://127.0.0.1:3030/api/datasources/uid/{uid}/health",
            headers={"Authorization": f"Bearer {token}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                health = json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            raise SystemExit(f"Grafana datasource {uid} health returned HTTP {exc.code}") from None
        except Exception as exc:
            raise SystemExit(f"Grafana datasource {uid} health failed: {type(exc).__name__}") from None
        if health.get("status", "").upper() != "OK":
            raise SystemExit(f"Grafana datasource {uid} is not healthy")
        print(f"Grafana datasource {uid}: OK")
PY
REMOTE

# Verify that the public gateway challenges unauthenticated MCP clients on the
# target itself. The protocol handshake above exercises the internal service;
# this guards the external Caddy route and its authentication boundary.
status="$(curl --connect-timeout 8 --max-time 15 -k --silent --output /dev/null --write-out '%{http_code}' \
  --resolve "observability.svc.plus:443:${TARGET_IP}" \
  "https://observability.svc.plus/mcp/${COMPONENT}/mcp")"
[[ "${status}" == 401 ]] || {
  echo "Unauthenticated MCP ingress /mcp/${COMPONENT}/mcp returned HTTP ${status}, expected 401." >&2
  exit 1
}
echo "${COMPONENT} MCP completed protocol checks and requires authentication at the HTTPS gateway."
