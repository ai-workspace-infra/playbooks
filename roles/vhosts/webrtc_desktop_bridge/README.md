# WebRTC Desktop Bridge Runtime

Prepares the optional system runtime for the standalone
`ai-workspace-lab/xworkmate-remote-desktop` server. It is disabled by the
parent AI Desktop profile unless `ai_desktop_webrtc_enabled: true` is set.

The initial standalone repository does not yet provide a server command, so
this role currently installs only the explicit X11 capture, H.264/GStreamer,
and input-injection dependencies. Binary and systemd deployment will be added
after the standalone signaling entrypoint exists.

X11 is the current backend. The role keeps backend selection explicit so a
future PipeWire/portal and libei Wayland implementation can replace it without
adding X11 dependencies back to `xworkmate-bridge`.

## Associated References

- Parent AI Desktop Role: [`roles/vhosts/ai_desktop`](../ai_desktop/README.md)
- IceWM Desktop Role: [`roles/vhosts/icewm_desktop_minimal`](../icewm_desktop_minimal/README.md)
- Remote Desktop Extraction: [`docs/migrations/remote-desktop-client-extraction.md`](file:///Users/shenlan/workspaces/xworkmate-app-ppa-series/docs/migrations/remote-desktop-client-extraction.md)

