# acp_codex

Codex ACP deployment role behind the unified `acp-server.svc.plus` ingress.

Installs:

- `caddy`
- `bubblewrap`

Exposes:

- raw Codex upstream: `codex app-server --listen ws://127.0.0.1:9001`
- public ACP bridge: `127.0.0.1:9010` via `acp-bridge-codex`
- public base URL: `https://acp-server.svc.plus/codex`

Notes:

- Caddy terminates TLS on `acp-server.svc.plus` and routes `/codex*` to this bridge.
- The Go ACP server serves `/acp` and `/acp/rpc` under the unified `/codex` prefix.
- `ACP_ALLOWED_ORIGINS` defaults to `https://xworkmate.svc.plus,http://localhost:*,http://127.0.0.1:*`.
