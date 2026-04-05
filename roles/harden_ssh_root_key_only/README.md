# harden_ssh_root_key_only

Harden SSH access for inventory hosts by installing the local operator public key for `root` and disabling password-based SSH authentication.

## What it does

- Reads `~/.ssh/id_rsa.pub` from the local machine by default
- Installs that public key into `/root/.ssh/authorized_keys`
- Writes an SSH daemon drop-in at `/etc/ssh/sshd_config.d/99-codex-root-key-only.conf`
- Disables password and keyboard-interactive SSH auth
- Reloads the SSH service after validating config syntax

## Variables

- `local_public_key_path`: path to the operator public key, default `~/.ssh/id_rsa.pub`
- `root_authorized_keys_path`: root authorized_keys path, default `/root/.ssh/authorized_keys`
- `sshd_dropin_dir`: SSH drop-in directory, default `/etc/ssh/sshd_config.d`
- `ssh_service_name_override`: optional service name override, otherwise auto-detects `ssh` or `sshd`

## Example playbook

```yaml
- hosts: all
  become: true
  roles:
    - role: harden_ssh_root_key_only
```

## Validation

After running the role, verify:

```bash
sshd -T | egrep '^(passwordauthentication|kbdinteractiveauthentication|pubkeyauthentication|permitrootlogin)'
passwd -S root
```

Expected:

- `PasswordAuthentication no`
- `KbdInteractiveAuthentication no`
- `PubkeyAuthentication yes`
- `root` remains accessible with the installed key
