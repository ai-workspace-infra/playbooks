# readonly_ssh_user

Create a remote SSH login user that can inspect the host as an unprivileged account but cannot modify protected system configuration.

## TLDR

export READONLY_SSH_USER_NAME=readonly Or auditor
export READONLY_SSH_LOCK_PASSWORD=true
export READONLY_SSH_ENABLE_SUDO=true
export READONLY_SSH_USER_PUBLIC_KEY='你的ssh公钥'

ansible-playbook -i inventory.ini create_readonly_ssh_user.yml --limit jp-xhttp-contabo.svc.plus

默认是 `readonly` profile。要创建更实用的“审计用户”，加上：

```bash
export READONLY_SSH_USER_PROFILE=audit
```

## What it does

- Creates a normal Linux user with no `sudo` or other privileged groups by default
- Locks the account password by default so password login is not usable
- Supports login with either a password hash or one or more SSH public keys
- Writes an `sshd_config.d` drop-in with per-user SSH restrictions
- Removes the user from common privileged groups such as `sudo`, `wheel`, and `docker`
- Optionally grants `sudo` for a tightly scoped read-only command whitelist
- Supports two profiles:
  - `readonly`: minimal read-only sudo whitelist
  - `audit`: broader inspection whitelist for logs, units, network, users, and common service status checks

## Important limitation

Linux 没法只靠“普通用户 + sudo 白名单”创建一个绝对不可变更的一般用途 SSH 审计用户。这个 role 提供的是“受限审计用户”，不是内核级只读沙箱。

This role creates an unprivileged user, not a kernel-enforced immutable sandbox. The user can still write to:

- its own home directory
- any path that is already world-writable or group-writable to one of its groups

It will not be able to change protected system configuration unless you explicitly grant extra privileges.

If you enable limited sudo, the user still does not join the `sudo` group. Instead, the role writes a dedicated sudoers file with only approved read-only commands.

For the default hardening mode used for `readonly`, the intended model is:

- `passwd -l` style locked password
- SSH public key login only
- no password-based SSH login
- account lifecycle managed by `root`

`audit` profile 适合：

- 看系统配置
- 看服务状态
- 看日志
- 看网络和用户状态
- 做人工巡检

`audit` profile 不适合：

- 真正执行 `ansible-playbook` 去修配置
- 依赖 `become` 修改远端文件
- 重启服务、安装包、改权限、写 `/etc`

如果目标是“能用 Ansible 修复”，那已经不是审计用户，而是受限运维用户，需要单独设计 sudoers 白名单。

## Variables

- `readonly_ssh_user_profile`: `readonly` or `audit`, default `readonly`
- `readonly_ssh_user_name`: username, default `readonly`
- `readonly_ssh_user_password_hash`: password hash for SSH password login
- `readonly_ssh_user_lock_password`: lock the local password, default `true`
- `readonly_ssh_user_authorized_keys`: list of SSH public keys for key-based login
- `readonly_ssh_user_groups`: supplementary groups to keep, default `[]`
- `readonly_ssh_user_manage_sshd`: whether to write an SSH Match block, default `true`
- `readonly_ssh_user_manage_sudoers`: whether to create a limited sudoers rule, default `false`
- `readonly_ssh_user_sudo_commands_readonly`: minimal whitelist
- `readonly_ssh_user_sudo_commands_audit`: broader audit whitelist
- `readonly_ssh_user_sudo_commands`: final whitelist, auto-derived from profile unless you override it
- `readonly_ssh_user_allow_tcp_forwarding`: default `false`
- `readonly_ssh_user_x11_forwarding`: default `false`
- `readonly_ssh_user_allow_agent_forwarding`: default `false`
- `readonly_ssh_user_force_command`: optional forced command

## Example playbook

```yaml
- hosts: jp_xhttp_contabo_host
  become: true
  roles:
    - role: readonly_ssh_user
      vars:
        readonly_ssh_user_profile: audit
        readonly_ssh_user_name: readonly
        readonly_ssh_user_manage_sudoers: true
        readonly_ssh_user_authorized_keys:
          - "ssh-ed25519 AAAA..."
```

## About ansible-playbook -D -C

这个角色创建的 `audit` 用户可以辅助“看”：

- 读取配置
- 看日志和 unit 状态
- 运行手工审计命令

但它不应该被视为可以可靠执行 `ansible-playbook -D -C` 来“修正 role / playbook”的用户。原因是很多 Ansible 任务即使在 check mode 下，仍然需要更广的 `become` 能力、远端临时文件操作和模块执行权限。

如果你需要“Ansible 可修复但受限”的账号，建议单独建一个 `operator_lite` 角色，而不是继续扩大审计账号权限。

## Password hash example

Generate a password hash locally:

```bash
python3 -c 'import crypt, getpass; print(crypt.crypt(getpass.getpass(), crypt.mksalt(crypt.METHOD_SHA512)))'
```

## Root-managed SSH-only mode

The default role behavior now matches this model:

- the `readonly` user has a locked password
- SSH login is expected to happen by public key only
- `PasswordAuthentication no` is enforced for that user in the SSH Match block
- password resets and account management should be performed by `root`
