# acp_opencode

OpenCode service plus internal ACP bridge consumed by `xworkmate-bridge`.

Installs:

- `caddy`

Exposes:

- `opencode serve --hostname 127.0.0.1 --port 38992 --print-logs`
- `xworkmate-go-core serve --listen 127.0.0.1:3910`

Notes:

- `xworkmate-app` must not call `/opencode`, `/opencode/acp`, or `/opencode/acp/rpc` public paths.
- Public app traffic goes through `wss://xworkmate-bridge.svc.plus/acp` or `https://xworkmate-bridge.svc.plus/acp/rpc`.
- Provider selection is exposed through bridge `acp.capabilities` and `xworkmate.routing.resolve`, not provider-specific public URLs.
