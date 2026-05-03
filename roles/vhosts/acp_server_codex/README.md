# acp_codex

Codex ACP deployment role for the internal runtime consumed by `xworkmate-bridge`.

Installs:

- `caddy`
- `bubblewrap`

Exposes:

- raw Codex upstream: `codex app-server --listen ws://127.0.0.1:9001`
- internal ACP listener: `127.0.0.1:9001`

Notes:

- `xworkmate-app` must not call a `/codex` public path.
- Public app traffic goes through `wss://xworkmate-bridge.svc.plus/acp` or `https://xworkmate-bridge.svc.plus/acp/rpc`.
- Provider selection is exposed through bridge `acp.capabilities` and `xworkmate.routing.resolve`, not provider-specific public URLs.
- `ACP_ALLOWED_ORIGINS` defaults to `https://xworkmate.svc.plus,http://localhost:*,http://127.0.0.1:*`.
