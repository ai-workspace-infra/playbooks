# Minimal AI Desktop

Installs the repository's minimal XFCE desktop and optional XRDP access. This
role is only the desktop base for CPA and CLI setup/debugging; it does not
install AI agents, monitoring, containers, or proxy services.

Set `ai_desktop_user_password` through inventory or an encrypted vars file when
`ai_desktop_manage_user` is enabled.

XRDP is optional. Set `ai_desktop_remote_enabled: false` to skip the XRDP
role and its Xorg server package while retaining the XFCE base.

The default connection method is `ai_desktop_remote_type: xrdp`, selected for
its broad native-client support. The desktop stack retains the XFCE panel and
terminal, Google Chrome on amd64 (Chromium fallback on other supported
architectures), CJK fonts, and the existing browser task's snap cleanup, apt
repository, launcher, and desktop shortcut handling.
