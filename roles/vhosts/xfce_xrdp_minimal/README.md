# xfce_xrdp_minimal

Minimal XFCE + XRDP bootstrap role for Ubuntu/Debian hosts.

## Scope

This role only:

- Updates apt cache
- Installs the minimal package set for XFCE and XRDP
- Disables `snapd` and snap-backed browser transitional packages
- Installs apt-managed `google-chrome-stable`
- Registers `google-chrome-xrdp.desktop` for HTTP, HTTPS, and `text/html`
- Enables and starts `xrdp` and `xrdp-sesman`
- Optionally validates service-unit availability after package install

It does not manage:

- Desktop user passwords
- XFCE tuning or session cleanup
- UFW rules, unless they are enabled through the package-only install path

## Default packages

The default package list is intentionally small:

- `xfce4-session`
- `xfce4-panel`
- `xfce4-terminal`
- `dbus-x11`
- `xserver-xorg-core`
- `xorgxrdp`
- `xrdp`
- `google-chrome-stable={{ xfce_google_chrome_version }}`

## Browser policy

The role intentionally does not install Ubuntu's `chromium-browser` or `firefox`
packages because current Ubuntu packages are snap transitional packages. It
instead:

- masks `snapd.service`, `snapd.socket`, `snapd.seeded.service`, and
  `snapd.apparmor.service`
- pins `chromium-browser`, `firefox`, and `snapd` with `Pin-Priority: -1`
- installs Google Chrome from Google's apt repository
- writes `/usr/local/bin/chromium-xrdp` as the XRDP-safe launcher
- points `/usr/local/bin/chromium` and `/usr/local/bin/chromium-browser` to
  `/usr/local/bin/chromium-xrdp`
- sets `HTTP`, `HTTPS`, and `text/html` handlers to
  `google-chrome-xrdp.desktop`
- sets `xdg-settings default-web-browser` to `google-chrome-xrdp.desktop`

## Example

```yaml
- hosts: vps
  become: true
  roles:
    - role: roles/vhosts/xfce_xrdp_minimal
```

## Notes

- If the host has just reinstalled `xrdp`, the role now checks for systemd unit files and runs `daemon-reload` before starting services.
- If the service units are still missing after install, the role fails with a clear message so the packaging issue can be fixed first.
- The role now writes `~/.xsession` for the target user and starts XFCE under `dbus-launch` so the RDP session keeps a usable desktop shell on Ubuntu.
- The default browser state is aligned with `jp-xhttp-contabo.svc.plus`: Google Chrome deb is the browser runtime, while `chromium` command names remain compatibility entry points to the same launcher.
