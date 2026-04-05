# gnome_xrdp_minimal

Minimal GNOME + XRDP role for Debian, Ubuntu, Fedora, and OpenSuse VPS hosts.

## What it does

- Installs a minimal GNOME desktop stack and XRDP
- Installs the Xorg backend needed by XRDP (`xserver-xorg-core`, `xorgxrdp`)
- Installs open-source Chromium browser
- Installs Chinese font support (`fonts-noto-cjk`)
- Creates `~/.xsession` for the target desktop user
- Enables `xrdp` and `xrdp-sesman`
- Optionally opens TCP `3389` with UFW if UFW is present
- Creates or updates the desktop user and ensures it has a usable local password for XRDP login

## Variables

- `gnome_user`: desktop login user, default `ubuntu`
- `gnome_packages`: minimal package list
- `gnome_enable_ufw`: whether to allow the RDP port with UFW
- `gnome_rdp_port`: RDP port, default `3389`
- `gnome_user_groups`: supplemental groups for the desktop user, default `["sudo"]`
- `gnome_user_password_plaintext`: required password for the desktop user so XRDP can authenticate
- Supported platforms: Debian/Ubuntu, Fedora, OpenSuse

## Example playbook

```yaml
- hosts: vps
  become: true
  roles:
    - role: roles/vhosts/gnome_xrdp_minimal
```
