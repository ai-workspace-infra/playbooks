# plasma_xrdp_minimal

Minimal Plasma + XRDP role for Debian, Ubuntu, Fedora, and OpenSuse VPS hosts.

## What it does

- Installs a minimal Plasma desktop stack and XRDP
- Installs the Xorg backend needed by XRDP (`xserver-xorg-core`, `xorgxrdp`)
- Installs open-source Chromium browser
- Installs Chinese font support (`fonts-noto-cjk`)
- Creates `~/.xsession` for the target desktop user
- Enables `xrdp` and `xrdp-sesman`
- Optionally opens TCP `3389` with UFW if UFW is present
- Creates or updates the desktop user and ensures it has a usable local password for XRDP login

## Variables

- `plasma_user`: desktop login user, default `ubuntu`
- `plasma_packages`: minimal package list
- `plasma_enable_ufw`: whether to allow the RDP port with UFW
- `plasma_rdp_port`: RDP port, default `3389`
- `plasma_user_groups`: supplemental groups for the desktop user, default `["sudo"]`
- `plasma_user_password_plaintext`: required password for the desktop user so XRDP can authenticate
- Supported platforms: Debian/Ubuntu, Fedora, OpenSuse

## Example playbook

```yaml
- hosts: vps
  become: true
  roles:
    - role: roles/vhosts/plasma_xrdp_minimal
```
