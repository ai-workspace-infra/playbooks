# xfce_xrdp_minimal

Minimal XFCE4 + XRDP role for constrained Ubuntu/Debian VPS hosts.

## What it does

- Installs only the required desktop and XRDP packages
- Installs the Xorg backend needed by XRDP (`xserver-xorg-core`, `xorgxrdp`)
- Installs open-source Chromium browser
- Installs Chinese font support (`fonts-noto-cjk`)
- Creates `~/.xsession` for the target desktop user
- Enables `xrdp` and `xrdp-sesman`
- Disables compositor and animations to reduce resource usage
- Optionally opens TCP `3389` with UFW if UFW is present
- Creates or updates the desktop user and ensures it has a usable local password for XRDP login

## Variables

- `xfce_user`: desktop login user, default `ubuntu`
- `xfce_packages`: minimal package list
- `xfce_enable_ufw`: whether to allow the RDP port with UFW
- `xfce_rdp_port`: RDP port, default `3389`
- `xfce_disable_compositor`: default `true`
- `xfce_disable_animations`: default `true`
- `xfce_user_groups`: supplemental groups for the desktop user, default `["sudo"]`
- `xfce_user_password_plaintext`: required password for the desktop user so XRDP can authenticate

## Example playbook

```yaml
- hosts: vps
  become: true
  roles:
    - role: roles/vhosts/xfce_xrdp_minimal
```

## Manual validation

Run these on the server after connecting through RDP:

```bash
systemctl status xrdp --no-pager --full
echo "$XDG_SESSION_TYPE"
free -m
passwd -S ubuntu
```

Expected:

- `xrdp` is active
- `XDG_SESSION_TYPE=x11`
- memory stays under the host budget
- `ubuntu` is not locked and can authenticate with the password you provided
