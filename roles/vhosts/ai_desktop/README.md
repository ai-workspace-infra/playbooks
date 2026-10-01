# Minimal AI Desktop

Installs the repository's minimal XFCE or IceWM desktop and optional XRDP or
WebRTC access. This role is only the desktop base for CPA and CLI
setup/debugging; it does not install AI agents, monitoring, containers, or
proxy services.

Select the desktop backend with `ai_desktop_desktop_type`: `xfce` is the
default; `icewm` uses the independent IceWM minimal role.

Set `ai_desktop_user_password` through inventory or an encrypted vars file when
`ai_desktop_manage_user` is enabled.

XRDP is optional. Set `ai_desktop_remote_enabled: false` to skip the XRDP
role and its Xorg server package while retaining the selected desktop base.

The default connection method is `ai_desktop_remote_type: xrdp`, selected for
its broad native-client support. Set it to `webrtc` to prepare the X11 capture
runtime used by the xworkmate bridge. IceWM provides a terminal, file manager,
and Google Chrome on amd64; set `ai_desktop_browser: chromium` to use the
distribution browser instead.

Each node is intentionally scoped to one account set:
`ai_desktop_account_scope: single`. Multi-account rotation or aggregation must
be deployed as separate nodes or an upstream gateway.

This is a strict minimal desktop base. Its package allowlist contains only the
XFCE session/window manager, panel, terminal, browser support, CJK fonts, and
the selected remote-access dependencies. It does not install or configure
office suites, Wine compatibility, video players, media suites, or other
heavyweight desktop add-ons. The existing browser task's snap cleanup remains
because it prevents snap-backed browser packages from being pulled in.

`openssh-server` is installed and enabled by default as the base maintenance
and provisioning connection. Disable it with `ai_desktop_sshd_enabled: false`
only when SSH is managed elsewhere.

WebRTC remote desktop is optional. `ai_desktop_remote_type: webrtc` enables the
capture/input runtime role without starting a second standalone desktop
server; signaling remains the responsibility of xworkmate-bridge. The
explicit `ai_desktop_webrtc_enabled` switch can also prepare those dependencies
alongside XRDP.
