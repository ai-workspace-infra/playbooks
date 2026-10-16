# Isolated Selfhost PROD V2

The only permitted targets are the four V2 hosts in the IaC-generated CMDB.
Existing Selfhost PROD and Serverless services and records are not deployment targets.
No workflow dispatch is used. Images are built off-host, pushed with full commit SHA
and digest, then selected by an immutable GitOps release tag.

## Inputs and ordering

Keep CMDB, inventory, source export, TLS keys, registry credentials and Ansible
extra-vars outside Git. Source users come from Selfhost PROD's `account` database,
not Serverless. Transfer snapshots contain password hashes and MFA state and must
have mode 0600. Do not transfer old sessions. Agent credentials are separate per
resource class; `resource:free`, `resource:plus`, and the dedicated team scope must
never share a wildcard enrollment credential.

Run from this repository with an IaC-generated inventory and protected extra-vars:

```sh
ansible-playbook deploy-prod-v2-control.yml -i "$V2_INVENTORY" -e @"$V2_VARS"
# FIRST INITIALIZATION ONLY: refuses a non-empty or live application database.
ansible-playbook migrate-prod-v2-accounts.yml -i "$V2_INVENTORY" -e @"$V2_VARS"
ansible-playbook complete-prod-v2-native-billing.yml -i "$V2_INVENTORY" -e @"$V2_VARS"
ansible-playbook configure-prod-v2-agent-auth.yml -i "$V2_INVENTORY" -e @"$V2_VARS"
ansible-playbook activate-prod-v2-control.yml -i "$V2_INVENTORY" -e @"$V2_VARS"
ansible-playbook configure-prod-v2-billing-ingress.yml -i "$V2_INVENTORY" -e @"$V2_VARS"
ansible-playbook deploy-prod-v2-edges.yml -i "$V2_INVENTORY" -e @"$V2_VARS"
ansible-playbook refresh-prod-v2-edge-config.yml -i "$V2_INVENTORY" -e @"$V2_VARS"
```

`prod_v2_accounts_dir` selects the reviewed Accounts source checkout;
`prod_v2_gitops_dir` selects the fixed GitOps checkout; `prod_v2_artifacts_dir`
selects protected downloaded release artifacts and migration inputs. These paths
must be supplied through the protected extra-vars. Doco-CD's poll reference is an
immutable release tag, not a moving branch. Preserve its Docker registry config
mount when invoking a partial playbook with `--start-at-task`.

Apply the subscription CSV through the Accounts plan-group batch API using an
administrator session: preview, then apply the preview token with a stable
request ID. Replay the same request and verify the ten persisted subscription
records. Email is the matching key. Blank source dates remain blank rather than
inventing an expiry. Verify account UUID, proxy UUID, password hash, MFA state and
active state against the protected source export.

## Runtime checks

- Accounts/Billing `/api/ping`: expected digest, managed native primary role and
  checkpoint 2026101502. Accounts `/readyz` must report its database ready.
- Billing owns the reviewed additive `cloud_vendor_costs` table. Do not disable
  its schema guard to start an incompatible image.
- PostgreSQL 5432 binds localhost; encrypted ingress 15433 is restricted to the
  V2 private subnet. Public database DNS alone does not grant access.
- Free/Plus/Team each run Caddy, XHTTP Xray, TCP Xray, scoped Agent and two
  exporters. XHTTP uses `/split` and a local Unix socket; no APISIX or common
  pool path is involved. Each agent reports its own actual public domain.
- Check both Xray client lists against the scoped user delivery API. Current
  migrated state is 19 Free, 10 Plus, 0 Team and one root excluded from proxy
  pools. Do not invent an additional Free account to reach 20.
- Connect real Free/Plus clients, confirm egress, reject cross-pool credentials,
  and verify traffic checkpoints, minute buckets and billing ledger in PG.
- Exporters have separate XHTTP/TCP node IDs and state files, preventing the two
  cumulative counter streams from resetting each other's checkpoints.
- Per-node `init.sh` is retained in root and SSH-user home with mode 0700. It
  intentionally includes only that node's runtime enrollment token, never a
  Vault root token. It reconciles installed services, not cloud resources.
- Remove temporary administrative validation sessions after acceptance. Keep
  migration exports and receipts protected outside Git.
