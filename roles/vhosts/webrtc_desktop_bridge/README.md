# WebRTC Desktop Bridge Runtime

Prepares the optional system runtime for the standalone
`ai-workspace-lab/xworkmate-remote-desktop` server. It is disabled by the
parent AI Desktop profile unless `ai_desktop_webrtc_enabled: true` is set.

The role installs runtime dependencies and, when service deployment is enabled,
installs the supplied binary and unit. Before deployment, provision a nonempty
token file owned by the service user with mode `0600`. The role only references
this file; it does not create, replace, or rotate the token. Keep token material
out of Git, inventories, logs and unit files.

The legacy `webrtc_desktop_bridge_token` input is rejected when nonempty. Remove
it from callers and provide `webrtc_desktop_bridge_token_file` instead; the
parent desktop password is no longer reused as the bridge token. Prepare and
validate the new token and its consumers before migrating an existing service.
For rollback, restore the previously reviewed role and unit while retaining the
active token file; never restore a unit containing a leaked token. A listener or
routing change requires a separate reachability and rollback plan.

X11 is the current backend. The role keeps backend selection explicit so a
future PipeWire/portal and libei Wayland implementation can replace it without
adding X11 dependencies back to `xworkmate-bridge`.

## Associated References

- Parent AI Desktop Role: [`roles/vhosts/ai_desktop`](../ai_desktop/README.md)
- IceWM Desktop Role: [`roles/vhosts/icewm_desktop_minimal`](../icewm_desktop_minimal/README.md)
- Remote Desktop Extraction: [`docs/migrations/remote-desktop-client-extraction.md`](file:///Users/shenlan/workspaces/xworkmate-app-ppa-series/docs/migrations/remote-desktop-client-extraction.md)

