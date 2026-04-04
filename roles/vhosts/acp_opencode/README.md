# acp_opencode

OpenCode web endpoint plus ACP bridge deployment role.

Installs:

- `caddy`

Exposes:

- `opencode serve --hostname 127.0.0.1 --port 38992 --print-logs`
- `xworkmate-go-core serve --listen 127.0.0.1:3910`
- `https://acp-server-opencode.svc.plus`
- `https://acp-server-opencode.svc.plus/acp`
- `https://acp-server-opencode.svc.plus/acp/rpc`

Notes:

- `/` stays on the OpenCode HTML UI.
- `/acp` and `/acp/rpc` are routed to the XWorkmate ACP bridge with CORS validation.
- The bridge advertises only the `opencode` provider by disabling other ACP provider binaries in the service environment.
