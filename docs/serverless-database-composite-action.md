# Serverless database composite action caller contract

`.github/actions/serverless-database-operation` packages the reviewed steps and
existing database scripts used by
`.github/workflows/serverless-database-operations.yml`. The reusable workflow
continues to call the local action inside its existing protected environment
job, so this owner-only refactor preserves its `job_workflow_ref` identity.

## Direct Toolkit callers

A future Toolkit caller may invoke the action only from a job that:

- checks out Playbooks at a full reviewed 40-character commit SHA and passes
  the same SHA as `owner_sha`; the action verifies the checkout before Vault;
- grants only `contents: read`, `actions: read`, and `id-token: write` as
  needed by the selected operation;
- selects the exact GitHub Environment (`uat` or `prod`) on that job and uses
  `cancel-in-progress: false` with an environment/correlation-specific
  concurrency key;
- supplies one validated environment, operation, correlation ID, and immutable
  release ref where required; baseline and migration remain UAT-only; and
- does not place database credentials in the action inputs or outputs.

Calling this composite directly from Toolkit changes the OIDC
`job_workflow_ref`. The current reusable-workflow identity is
`ai-workspace-infra/playbooks/.github/workflows/serverless-database-operations.yml@<pinned-playbooks-sha>`.
For the intended Toolkit caller, the narrow replacement claim is the exact
the exact caller `workflow_ref`, repository and environment claim after the
direct workflow's actual OIDC claims have been observed. Do not assume a
`job_workflow_ref` claim exists for a direct job. UAT and PROD remain separate
roles and secret-path policies. Any required workflow-path change must be reviewed through the Vault
configuration-as-code owner and applied by an authorized operator before the
Toolkit caller switches. This PR does not edit or apply Vault roles.

The action writes a metadata-only operation receipt containing the caller
repository, SHA, workflow ref, run ID, run attempt, environment, operation,
correlation ID, and result. It never includes a DSN, token, password, backup
payload, or application credential. A backup/restore receipt remains a separate
trusted artifact and is still validated by `verify_checkpoint_receipt.py`;
the operation receipt is not backup proof.

SIT is deliberately unsupported by this action. Adding SIT requires a separate
owner-reviewed database, Vault identity, secret path, and environment-isolation
contract; callers must not map SIT to UAT.

The existing `checkpoint` operation is not read-only: its Supabase path creates
or updates a checkpoint ledger in the target database. The extraction preserves
that existing behavior. Ordinary deploy must not silently invoke it as a read-only
probe; its authorization and DB identity require separate verification.
