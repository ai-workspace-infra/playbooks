# ZITADEL server operations

Read-only service operations for the ZITADEL IAM stack deployed by
`roles/docker/zitadel` (`deploy_iam_domain.yml` → `deploy_zitadel_docker.yaml`).
This role owns ZITADEL service health and failure diagnostics. The Toolkit
workflow selects the target from GitOps, holds the Vault/OIDC identity, obtains
host access from IaC Modules and judges the result; it no longer carries its
own copy of these checks.

Entrypoint: `zitadel_operations.yml`.

## Operations

| `zitadel_operation` | Runs on | Checks |
| --- | --- | --- |
| `verify_host` | exactly one IAM host (become) | API and Login containers `healthy`; Login client PAT present (bootstrap completed); Caddy active; API answers on loopback; Caddy serves verified TLS OIDC discovery for the domain; then the public check below. On any failure it collects read-only evidence and fails. |
| `verify_public` | the controller only | `https://<domain>/.well-known/openid-configuration` names issuer `https://<domain>` and serves `jwks_uri` under it. No host access needed. |

## Inputs

Required:

- `zitadel_operation`: `verify_host` or `verify_public`.
- `zitadel_operations_target`: exactly one inventory host name (`localhost` for
  `verify_public`). A missing, unknown or pattern target fails instead of
  matching no hosts.
- `zitadel_operations_domain`: the declared public IAM domain.

Optional (defaults mirror `roles/docker/zitadel`):

- `zitadel_operations_workspace` (`/opt/zitadel`)
- `zitadel_operations_api_port` (`19080`)
- `zitadel_operations_caddy_conf_dir` (`/etc/caddy/conf.d`)
- `zitadel_operations_ready_timeout_seconds` (`180`),
  `zitadel_operations_ready_poll_seconds` (`5`)

```bash
# After deploy_iam_domain.yml, against the same single host:
ansible-playbook -i inventory.json zitadel_operations.yml \
  -e zitadel_operation=verify_host -e zitadel_operations_target=iam-shared-0 \
  -e zitadel_operations_domain=iam.svc.plus

# Verify only, without host access:
ansible-playbook -i localhost, -c local zitadel_operations.yml \
  -e zitadel_operation=verify_public -e zitadel_operations_target=localhost \
  -e zitadel_operations_domain=iam.svc.plus
```

## Safety

- Nothing here restarts, reloads, reconfigures or resets a service. The
  masterkey, PostgreSQL, Login client PAT and unbootstrapped-reset protections
  stay in `roles/docker/zitadel` and are unchanged.
- The Login client PAT is only checked for presence; its content is never read.
- Diagnostics redact bearer, GitHub and Vault tokens and can never turn a failed
  verification into a pass.
