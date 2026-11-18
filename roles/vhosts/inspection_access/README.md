# Inspection access

Creates `auditor` (SSH key only, no privileged groups or forwarding) and
`db_readonly` (local container socket only, no password, SELECT only in one
explicit database). Supply public keys, database/container and table owners
from the selected environment. No secret or deployment identity is embedded.

Run `configure_inspection_access.yml` with CMDB inventory and an exact limit.
A verified cloud-control-plane one-shot inventory is also supported for
explicit maintenance. No service/container restart is required.

The auditor can run normal unprivileged system checks and fixed sudo
commands: `platform-inspect containers`, `container-logs`, `logs`, `services`,
`processes`, and `database`. Logs are capped at 200 lines with no pager.
Container names are supplied on stdin and validated, never interpreted as options.
SQL is supplied on stdin; the gateway connects as the database reader, never
as an administrator. It cannot execute psql meta commands or arbitrary Docker
operations. SQL is transaction read-only, and ACLs independently deny writes.
Existing and future public tables/sequences receive SELECT; existing RLS
tables get a SELECT-only inspection policy. Incompatible existing identities
or executable security-definer routines stop provisioning for review.

Examples after login:

```sh
id; uptime; df -h; free -m; systemctl --no-pager --failed
sudo -n /usr/local/sbin/platform-inspect containers
sudo -n /usr/local/sbin/platform-inspect services
sudo -n /usr/local/sbin/platform-inspect processes
sudo -n /usr/local/sbin/platform-inspect logs
printf web-saas-accounts | sudo -n /usr/local/sbin/platform-inspect container-logs
printf 'SELECT count(*) FROM users' | sudo -n /usr/local/sbin/platform-inspect database
```

Ordinary users can write their home and shared temporary directories; this
is an audit account, not a filesystem sandbox. Sensitive runtime config,
container environment and arbitrary root command execution are unavailable.
Logs may contain sensitive application data; restrict authorized keys to approved auditors.
No role membership, ownership, write or administrative rights are granted.

Rollback: remove the dedicated sudoers/SSH drop-ins, lock the OS account,
and revoke reader CONNECT/SELECT and future grants. Do not drop owned data
or unrelated migration roles. Future RLS tables require explicit policy review.
