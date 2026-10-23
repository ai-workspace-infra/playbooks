# V5 deployment migration idempotence batch

This prepares the Playbooks regression batch for candidate `prod-v5` with source
`prod-v4`. The planner emits case paths, historical pins and outcomes, required
new pins, exit policy, evidence locations, and the two Playbooks entrypoint
commands. It is offline: it does not call Ansible, Docker, a database, DNS, or a
cloud API.

## Prepare the manifest

From the Playbooks checkout, run:

```sh
python3 scripts/prepare_generation_regression_batch.py \
  --target-generation prod-v5 \
  --source-generation prod-v4 \
  --output /tmp/v5-generation-regression-batch.json
```

Without the target candidate and runtime evidence, this deliberately emits a
`BLOCKED` manifest with the exact missing inputs. It is not an acceptance result.
After the deployment owner has produced the immutable V5 refs and isolated
source evidence, pass the values with the corresponding options shown by
`python3 scripts/prepare_generation_regression_batch.py --help`. Public A-record
expectations must come from the pinned stage declaration and include exactly
the five declared public hosts. They are never inferred from a V3/V4 convention.

The V4 business writer continues serving and accepting writes throughout stages
0-7. This batch does not pause, fence, or mutate that live writer. Migration reads
use a dedicated read-only identity/transaction against the retained isolated
source snapshot. Disposable PostgreSQL integration fixtures are freshly created
with Docker network mode `none`; their isolation evidence is separate from both
the source-reader evidence and live V4 writer ownership.

The manifest separates three refs: the Playbooks history pin below, the
candidate GitOps commit recorded by the host runner, the fixed Playbooks runner
SHA, and the Accounts test/tool source SHA recorded by the PostgreSQL fixture
runner. Do not substitute one for another. The Accounts SQL/test source paths
and checksums and the immutable PostgreSQL image are also required inputs.

## Executable batch after gates

The generated manifest includes these candidate-only invocations:

```sh
ansible-playbook -i <verified-v5-inventory> web-saas-generation-regression.yml \
  -e @<protected-v5-regression-vars.json>

ansible-playbook -i <verified-v5-inventory> web-saas-generation-postgres-cases.yml \
  -e @<protected-v5-regression-vars.json>
```

The protected vars file supplies the exact V5 target group, adjacent V4 source,
candidate GitOps SHA, source SHA, route expectation file, unique target checkpoint,
retained tool and SQL paths/checksums, and fixture image digest. It also carries
the isolated source-restore receipt, read-only migration-access evidence, and
fixture-isolation evidence. Secrets remain runtime Vault reads. Use a verified
one-host V5 inventory. The PostgreSQL entrypoint creates uniquely named,
network-isolated fixture containers on the V5 target; receipts record
`fixture_network_isolation=network-none` and
`live_v4_writer_fence_action=not_performed`. Secondary fixtures, when used, share
the isolated namespace. Do not run this on the workstation or against existing
containers. In the schema comparison,
`--writers-paused` confirms that candidate/target writers are paused; it does not
assert that the live V4 source writer is paused. Neither entrypoint writes to V4
or authorizes cloud changes, DNS changes, or traffic/writer cutover.

## Case and receipt contract

The manifest expands the ten `regression_cases.py` host cases and six
`postgres_cases.py` integration selectors listed by the 2026-10-21 checklist.
For each host case, the historical source is pinned to Playbooks commit
`e2b584716fd6770f64d2ba2a7c4137fa4117c7ae`; the manifest records the SHA-256 of
both historical scripts. Each case also records its original Knowledge case
path, expected behavior, historical exit observations, and new target evidence
path. PostgreSQL rows include the exact Go test selector, DSN environment key,
historical receipt path, and source file where resolved.

The checklist records the later schema comparison candidate only as SHA prefix
`765ea`, and does not publish the old Accounts test/tool, SQL, or PostgreSQL
image digests. The manifest records those as pin gaps; the new V5 batch must use
fresh full refs and hashes. The exact private per-case JSON filenames are also
not present in the shared checklist, so historical evidence is linked by its
known checkpoint and receipt names without asserting inaccessible file paths.

The host runner uses `PASS=0`, `FAIL=1`, `BLOCKED=2`, and `NOT_RUN=null`.
The PostgreSQL runner uses `PASS=0`, `FAIL/BLOCKED=1`, and `NOT_RUN=null`; a
skipped Go test is not a pass. New receipts use the V5 checkpoint and candidate
GitOps SHA. Existing private V4 files are referenced as historical evidence and
are never opened or modified by this preparation.

Two checklist gates are retained outside the 16-script-case batch:

- `business-runtime-role` has the candidate-only acceptance script
  `tests/data_operations/selfhost/v4_business_role_acceptance.sh`; it now
  requires explicit `GENERATION_TARGET_GENERATION` and
  `GENERATION_POSTGRES_CONTAINER` values and has no container default. Its V4
  baseline is partial and does not prove configuration convergence for V5.
- `writer-ownership` has no identified executable isolated case or complete
  receipt in the source records, so it is deferred to manual cutover stage 8.

The lifecycle test source is resolved in the latest Accounts `origin/main` at
`a058ae6dbd95c94f9c337dd4f2cd9f30c1fff145`: `internal/migrate/subscription_upgrade_postgres_test.go`
(`TestSubscriptionLifecycleUpgradePostgres17`). The manifest records that
discovery pin and the source-file SHA-256; the actual candidate Accounts source
SHA remains an explicit runtime pin.

The historical writer-ownership row has no executable runner or complete
receipt. It is therefore deferred, not treated as an unresolved prerequisite for
this stages 0-7 batch. Before separately approved manual cutover stage 8, the
owner must provide and pin a writer-ownership acceptance case; only that stage
fences the live V4 business writer.

## Required gates and limits

The generated manifest remains blocked until it has all of the following:

- Full candidate GitOps SHA, Playbooks runner SHA, and Accounts source SHA.
- Verified one-host inventory, V5 CMDB instance ID, and candidate PostgreSQL
  container name. The lifecycle Go test path is fixed in the manifest from the
  latest Accounts main checkout.
- Protected vars-file path, unique run ID, target IPv4 address, and target schema
  version/checksums.
- Public route expectation JSON from the pinned stage declaration.
- New target-local checkpoint path and retained integration binary with SHA-256.
- Immutable PostgreSQL image digest and billing/lifecycle SQL paths with hashes.
- Isolated V4 source-restore receipt; evidence that the dedicated migration
  identity/transaction is read-only; and evidence that the disposable test
  fixtures are isolated. These do not pause or fence the live V4 business writer.

The V4 business-writer fence and its acceptance evidence belong to manual
cutover stage 8, not this regression batch. Its runner path and immutable pin
must be supplied before that later stage, but are not required to prepare or run
the stages 0-7 regression batch.

The offline local fixtures are:

```sh
python3 -m unittest discover -s tests -p 'test_prepare_generation_regression_batch.py' -v
<ansible-python> -m unittest discover -s tests -p 'test_recovery_public_ingress_idempotency.py' -v
<ansible-python> -m unittest discover -s tests/data_operations/selfhost -p 'v4_acceptance_scripts_test.py' -v
```

Use the Python interpreter reported by `ansible-playbook --version` for suites
that import Ansible's Jinja/YAML libraries. The system `python3` may not include
those packages.

The preparation does not run migration tests because this host has pre-existing
Docker containers and the integration fixture runner is designed to create
containers on the verified candidate. Offline planner contract tests are safe
to run locally and do not claim database migration, deployment, or V5 runtime
acceptance.
