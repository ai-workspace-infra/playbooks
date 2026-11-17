# readonly audit checklist

Use this checklist after creating a `readonly` or `audit` SSH user with the `readonly_ssh_user` role.

The goal is to confirm the account can inspect system information and logs, but cannot make real changes such as editing protected files, restarting services, installing packages, or changing permissions.

## 1. Login

```bash
ssh readonly@jp-xhttp-contabo.svc.plus
```

## 2. Verify identity and sudo scope

These commands should succeed:

```bash
whoami
id
sudo -l
```

Expected:

- current user is `readonly`
- user is not in `sudo`, `wheel`, `docker`, or other privileged groups unless explicitly intended
- `sudo -l` shows only the approved read-only whitelist

## 3. Verify system and environment inspection

These commands should succeed:

```bash
uname -a
hostnamectl status
uptime
free -h
df -h
sudo env
```

Expected:

- system identity and runtime information are visible
- no write action is performed

## 4. Verify user, session, and scheduled task inspection

These commands should succeed:

```bash
sudo getent passwd | head
sudo last -n 20
sudo lastlog | head
sudo crontab -l -u root
```

Expected:

- account and login history can be reviewed
- root cron can be inspected if included in the sudo whitelist

## 5. Verify network inspection

These commands should succeed:

```bash
sudo ip addr
sudo ip route
sudo ss -tulpn
sudo iptables -S
```

Expected:

- interface, route, listening port, and firewall rule information is visible

## 6. Verify service and log inspection

These commands should succeed:

```bash
sudo systemctl status ssh
sudo systemctl list-units --type=service --no-pager | head -50
sudo journalctl -u ssh -n 50 --no-pager
```

Expected:

- service status is visible
- logs can be inspected
- no service control actions are available

## 7. Verify configuration file inspection

These commands should succeed:

```bash
sudo cat /etc/ssh/sshd_config
sudo cat /etc/passwd
sudo nginx -T
```

Expected:

- protected configuration can be inspected through approved read-only commands
- config dump commands such as `nginx -T` work if the binary exists and is whitelisted

## 8. Verify container inspection if Docker is present

These commands should succeed when Docker is installed and included in the whitelist:

```bash
sudo docker ps
sudo docker images
sudo docker inspect <container_or_image>
```

Expected:

- container and image metadata is visible
- no container lifecycle actions are permitted

## 9. Verify modification attempts are blocked

These commands should fail:

```bash
sudo systemctl restart ssh
sudo systemctl reload nginx
sudo touch /etc/readonly-test
sudo cp /etc/hosts /etc/hosts.bak2
sudo chmod 644 /etc/ssh/sshd_config
sudo useradd test123
sudo apt install -y tree
```

Expected:

- restart and reload commands fail
- writes under `/etc` fail
- permission changes fail
- package installation fails
- user creation fails

## 10. Interpretation

The account is behaving correctly if:

- inspection commands succeed
- modification commands fail
- the account can read most operational information needed for audits
- the account still cannot apply real remediation

If `ansible-playbook -D -C` is the target use case, this account is still not the right choice for remediation. It is an audit account, not a limited operator account.

## 11. Optional follow-up

If the account is too weak for your audit process:

- add only specific additional read-only commands to the sudo whitelist
- avoid broad additions such as editor binaries, package managers, service restart commands, shell escapes, or file write utilities

If the account is too strong:

- remove commands from the `audit` whitelist
- create a stricter profile derived from `readonly`
