# IceWM Minimal Desktop

An intentionally small, independent desktop role for Debian-family hosts. It
uses `icewm-session` and provides the desktop plumbing, terminal, file
manager, Google Chrome on amd64 (or Chromium when selected/fallback), and
optional XRDP or WebRTC runtime access needed for setup and CLI debugging.

It does not itself install XFCE, office software, Wine, video players, media
suites, or monitoring agents. AI Agent CLIs and runtimes come from its
dependency role [`ai_desktop_addon`](../ai_desktop_addon/README.md) (declared in
`meta/main.yml`), which runs before this role. The configuration is intentionally small:
IceWM preferences, preference overrides (`prefoverride`), single-desktop bottom
panel toolbar (`终端 ｜ 文件浏览器 ｜ Web浏览器（Chrome）`, with virtual desktop
switching disabled), application menu, key bindings, XWorkTech start menu logo
(`taskbar/icewm.xpm`), and wallpaper (`xworktech-wallpaper.png`) are installed
system-wide under `/etc/X11/icewm/` and `/usr/share/icewm/` (and into the
user's `~/.icewm/` when `icewm_desktop_manage_user` is enabled) instead of
copying a complete desktop theme.

Set `icewm_desktop_browser: chromium` to stay entirely on distribution
packages. The default `chrome` downloads the official amd64 Google Chrome
package from `icewm_desktop_chrome_download_url`; pin it with
`icewm_desktop_chrome_checksum` in production.

## Associated References

- Parent AI Desktop Role: [`roles/vhosts/ai_desktop`](../ai_desktop/README.md)
- Remote Desktop Server: [`roles/vhosts/remote_desktop_xrdp_server`](../remote_desktop_xrdp_server/README.md)
- WebRTC Bridge Runtime: [`roles/vhosts/webrtc_desktop_bridge`](../webrtc_desktop_bridge/README.md)

