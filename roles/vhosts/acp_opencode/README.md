# acp_opencode

OpenCode service plus ACP bridge behind the unified `xworkmate-bridge.svc.plus` ingress.

Installs:

- `caddy`

Exposes:

- `opencode serve --hostname 127.0.0.1 --port 38992 --print-logs`
- `xworkmate-go-core serve --listen 127.0.0.1:3910`
- `https://xworkmate-bridge.svc.plus/opencode`
- `wss://xworkmate-bridge.svc.plus/opencode/acp`
- `https://xworkmate-bridge.svc.plus/opencode/acp/rpc`

Notes:

- `/opencode` stays on the OpenCode HTML UI through the unified Caddy prefix.
- `/opencode/acp` and `/opencode/acp/rpc` are routed to the XWorkmate ACP bridge with CORS validation.
- The bridge advertises only the `opencode` provider by disabling other ACP provider binaries in the service environment.
