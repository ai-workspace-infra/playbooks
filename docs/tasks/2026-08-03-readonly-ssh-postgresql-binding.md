# Read-only SSH and PostgreSQL binding

## Scope

This change adds an opt-in audit/migration identity to the `readonly_ssh_user`
role. It is intended for UAT validation first and is not enabled by default
on production hosts.

The default identity in `create_readonly_ssh_user.yml` is `readonly`:

```text
SSH public key -> Linux user readonly -> PostgreSQL role readonly
```

The same root public key may be supplied to `readonly_ssh_user_authorized_keys`,
but the key is bound to the ordinary user only. The account is not added to
`sudo`, `docker`, `adm`, `wheel`, or other privileged groups. Password login,
TCP forwarding, X11 forwarding, and agent forwarding remain disabled.

## PostgreSQL permissions

When `readonly_ssh_user_manage_postgresql=true`, the role requires a separate
Vault-managed PostgreSQL password. The SSH private key is never reused as the
database password.

The PostgreSQL role is configured as:

- `LOGIN`, `NOSUPERUSER`, `NOCREATEDB`, `NOCREATEROLE`, `NOREPLICATION`,
  `NOBYPASSRLS`, `NOINHERIT`;
- `CONNECT` on the configured databases;
- schema `USAGE` and table/sequence `SELECT` only;
- default `SELECT` privileges for the configured database owners;
- no sudo and no write access to protected service directories.

The playbook defaults the database list to the shared UAT databases:
`postgres`, `account`, `gitea`, `litellm`, `rag`, `vault_storage`, and `zitadel`.
Override this list explicitly for another environment.

## UAT-only validation

Supply the root-equivalent public key through `READONLY_SSH_USER_PUBLIC_KEY`
and the PostgreSQL credentials through Vault/Ansible variables. Do not put
private keys or password values in GitHub Actions inputs or repository files.

```bash
export READONLY_SSH_USER_NAME=readonly
export READONLY_SSH_USER_PUBLIC_KEY='ssh-ed25519 AAAA...'
export READONLY_SSH_LOCK_PASSWORD=true
export READONLY_SSH_MANAGE_POSTGRESQL=true
export READONLY_SSH_POSTGRES_PASSWORD='from-vault'
export READONLY_SSH_POSTGRES_ADMIN_PASSWORD='from-vault'

ansible-playbook -i inventory.ini create_readonly_ssh_user.yml \
  --limit console-uat.onwalk.net
```

The command is an example only; credentials must be injected by the UAT
deployment secret path. Production execution requires a separate approval.
