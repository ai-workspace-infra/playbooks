# xfce_xrdp_minimal

Minimal XFCE + XRDP bootstrap role for Ubuntu/Debian hosts.

## Scope

This role only:

- Updates apt cache
- Installs the minimal package set for XFCE and XRDP
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
