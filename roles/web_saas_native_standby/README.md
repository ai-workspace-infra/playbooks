# PROD native database standby

`setup-web-saas-native-standby.yml` prepares only the canonical IaC-owned
`open-platform-prod / web-saas-prod` PostgreSQL target. The caller must first
verify the successful approved resource run, immutable execution/GitOps commits
and exact artifact provenance. It passes that existing CMDB and its inventory,
not a handwritten inventory or replacement CMDB.

Runtime inputs are `DEPLOY_ENV=prod`, `CMDB_FILE`, `GITOPS_CHECKOUT`,
`GITOPS_COMMIT` and Vault-provided `POSTGRES_PASSWORD`, `GHCR_TOKEN` and
`GHCR_USERNAME`. The execution owner authenticates the private prebuilt image
pull through password-stdin with secret logging disabled. No connection
string/password/token is a dispatch input.

The fixed-SHA `.github/actions/prod-native-standby` entry point installs isolated,
pinned Ansible dependencies and consumes the original resource inventory and
same-run IaC access file. Before host execution it checks the approved CMDB
checksum, exact target/key/runner attempt and a fresh runner receipt path. Its
sanitized receipt binds the exact GitOps commit, independent disk and PostgreSQL
major. The caller must always invoke IaC access cleanup, including on failure;
neither private access files nor credentials may be uploaded as evidence.

Before host changes, the playbook binds the CMDB host/IP/SSH identity and source
commit and rejects running application/reconciler containers. Stopped writers
must also have restart disabled. It installs the existing Docker role, reuses
the exact independent-disk role, and projects only `postgres` from the fixed
GitOps compose plus PROD storage override. The derived private JSON is an
execution output. It is not a second manually maintained compose declaration.
Its sole credential file uses Compose `format: raw` so password punctuation,
quotes and dollar signs are preserved literally; unsupported Compose versions
refuse the project. Multiline values are rejected before rendering.

It starts only PostgreSQL, on loopback 5432 and `/data/postgresql`; it never
starts Doco-CD, Accounts, Billing, Console, Bridge, schema bootstrap or seeds.
Retries refuse a different image, unowned existing cluster or credentials, and
changed standby ownership inputs. PostgreSQL must be the qualified major 17;
an older existing database is not automatically upgraded. The database empty
guard covers relations/functions and user enum/domain/range types in every
application schema, excluding extension-owned objects.

`native_standby_receipt` proves target standby only. `schema_initialized` and
`database_cutover_approved` remain false. The next controlled stage consumes the
qualified Accounts image's `migratectl init`, then performs readonly source
copy and full business equality while target services/CD stay paused. Billing's
additional schema and protection against application startup seed/Proxy UUID
changes still require separate qualification before service startup.

Local guard/source checks are fictional fixtures. A merged owner, qualified
caller and successful real PROD execution are all still required.
