# acp_opencode

OpenCode web endpoint deployment role.

Installs:

- `caddy`

Exposes:

- `opencode serve --hostname 127.0.0.1 --port 38992 --print-logs`
- `https://acp-server-opencode.svc.plus`

Notes:

- This role exposes the OpenCode web UI, not the XWorkmate ACP JSON-RPC endpoint.
- Validation checks assert an HTML response marker so the role does not get confused with the Codex ACP bridge role.
