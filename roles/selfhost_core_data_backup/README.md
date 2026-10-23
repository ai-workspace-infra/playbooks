# API backup gate producer

Caller: `selfhost-core-data-backup.yml`, one CMDB-bound target with applications
and reconciler already paused. No source host commands are executed.

Inputs: `selfhost_core_data_backup_{target,resource_id,storage_profile,owner_sha,
vault_addr,key_ref,request,plan_sha256,receipt_file,proof_file}`. `request` is a
`migration.py` status request for the original plan and pinned Accounts client.
`key_ref` is an environment-scoped `kv/...#field` reference; never a password.
Also supply native `web_saas_data_backup_{release_tag,run_id,expected_version,
authorized_subscription_sample_id}` audit inputs. Receipt/proof parent directory
must already be protected and paths must be fresh. Existing files are not replaced.

The role calls the existing encrypted backup and independent restore roles,
opting into a full-database schema/archive, explicit root storage if declared,
and honest empty-target restore support. Defaults of existing callers are unchanged.
After restore it re-previews the same source snapshot with a new plan ID through
the pinned API client, while target writers remain paused. Native whole-public
row/sequence and full-schema equality plus the unchanged fresh canonical API
baseline binds the archive to the original API plan. The target adapter rehashes
the actual encrypted archive, checks all paired evidence and physical DB identity,
then emits Accounts `MigrationBackupReceipt` exactly. It never relabels a native
checksum as an API fingerprint. The proof reports whether data was populated;
an empty restore is not populated-data acceptance.

The target listener must explicitly consume the issued `receipt_file` with
`migratectl serve --backup-receipt-file`. Starting/restarting that opt-in listener,
initializing its receipt schema and Caddy routing remain separate reviewed ops.
Fetch only nonsecret receipt/proof evidence through the caller's authenticated
channel; verify its owner revision/run and proof checksum before apply. Do not
turn an arbitrary hand-authored JSON file into a production backup claim.

No gate is issued on failed archive, restore, changed baseline/schema/tool or
writer checks. Native backup/restore may leave protected archive/drill evidence;
do not erase it to retry. Use a new run ID. Runtime acceptance and a populated
recovery drill remain separate from unit fixtures and a Draft PR.
