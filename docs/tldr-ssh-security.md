# TLDR: SSH Security & Hardening Playbook

Quick reference for SSH security hardening, firewall controls, Fail2ban management, and connection checking.

## 1. SSH Hardening (Key-Only Auth)
Password login is completely disabled for all users. Direct root login is restricted to key-only.

### Configuration file
Drop-in config is deployed to:
`/etc/ssh/sshd_config.d/00-disable-password.conf`

```text
PasswordAuthentication no
PubkeyAuthentication yes
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
```

### Apply Changes
If you update SSH configurations, reload sshd:
```bash
# Debian/Ubuntu
sudo systemctl reload ssh

# RedHat/CentOS
sudo systemctl reload sshd
```

---

## 2. Fail2ban Management
Fail2ban monitors SSH authentication failures and bans offensive IPs.

### Default Settings
*   **Bantime**: 24 hours (`86400` seconds)
*   **Findtime**: 10 minutes (`600` seconds)
*   **Maxretry**: 3 attempts

### Useful Commands
```bash
# Check Fail2ban service status
sudo systemctl status fail2ban

# Check sshd jail status (banned IPs)
sudo fail2ban-client status sshd

# Unban a specific IP
sudo fail2ban-client set sshd unbanip <IP>

# Manually ban a specific IP
sudo fail2ban-client set sshd banip <IP>

# View fail2ban logs
sudo tail -f /var/log/fail2ban.log
```

---

## 3. SSH Proxy Connection Helper (`ssh_check.exp`)
A generic `expect` helper script to verify ProxyJump-ed SSH connectivity.

### Usage
To prevent password leaks in shell history (`~/.bash_history` or `~/.zsh_history`), **never** pass the password as a command-line argument. Instead, use one of the secure methods below:

#### Option A: Read securely from input (Recommended)
```bash
# Type your password securely (input will not echo on screen)
read -s SSH_CHECK_PASSWORD
export SSH_CHECK_PASSWORD

# Run the helper script (picks up password from env var)
ssh_check.exp admin@tky-proxy.svc.plus root@167.179.110.129
```

#### Option B: Set via env var with leading space
If your shell is configured to ignore commands starting with a space (e.g. `HISTCONTROL=ignorespace` in bash or `setopt HIST_IGNORE_SPACE` in zsh), you can set the variable with a leading space:
```bash
 export SSH_CHECK_PASSWORD="your_password"
 ssh_check.exp admin@tky-proxy.svc.plus root@167.179.110.129
```

#### Option C: Legacy/Direct (Not recommended, leaves history trace)
```bash
ssh_check.exp admin@tky-proxy.svc.plus root@167.179.110.129 "your_password"
```

---

## 4. Firewall (UFW) quick-ref
Used on hosts to manage ports (e.g. 80, 443, 1443).

```bash
# View firewall rules with line numbers
sudo ufw status numbered

# Allow a port to Anywhere
sudo ufw allow 443/tcp

# Delete a rule by rule number
sudo ufw delete <rule_number>

# Restrict port 22 to a specific IP (e.g. Proxy IP)
sudo ufw allow from 43.207.194.92 to any port 22 proto tcp
sudo ufw delete allow 22/tcp

# Reload firewall
sudo ufw reload
```
