# acp_codex

Codex ACP deployment role with a public XWorkmate ACP Web endpoint.

Installs:

- `caddy`
- `bubblewrap`

Exposes:

- raw Codex upstream: `codex app-server --listen ws://127.0.0.1:9001`
- public ACP Web server: `127.0.0.1:9010` via `xworkmate-go-core serve`
- public HTTPS endpoint: `https://acp-server-codex.svc.plus`

Notes:

- Caddy terminates TLS and proxies the public domain to the Go ACP server.
- The Go ACP server serves `/acp` and `/acp/rpc`.
- `ACP_ALLOWED_ORIGINS` defaults to `https://xworkmate.svc.plus,http://localhost:*,http://127.0.0.1:*`.
