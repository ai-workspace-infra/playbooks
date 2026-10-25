# V5 read-only acceptance observations

Owner: Playbooks. Caller: explicit SSH/Ansible execution first; no new GitHub workflow.
This role implements the first read-only subset of the 23-suite acceptance plan,
not full migration, business E2E or recovery acceptance. It never restarts services,
changes Kubernetes resources, writes business data or reads secret payloads.

Use `v5-readonly-acceptance.yml` with exactly one target resolved through
`inventory/terraform_cmdb.py`. Supply `v5_acceptance_target`, namespace, database
pod/user/name, trusted expected machine ID, expected schema, expected Agent count,
fixed GitOps SHA, Flux source name/namespace, and an absolute controller output
path using the `v5_acceptance_*` variables in defaults. No host or secret defaults
are provided. Machine ID must be independently recorded, not learned and accepted
inside the same run. Existing-host diagnostic execution must not be promoted to
CMDB-backed acceptance without fresh cloud/CMDB verification.

```sh
ANSIBLE_CONFIG=/path/to/reviewed-strict-ssh.cfg ansible-playbook \
  -i inventory/terraform_cmdb.py v5-readonly-acceptance.yml \
  --private-key /path/to/authorized/key -e @/path/to/nonsecret-acceptance-vars.json
```

The reviewed controller config must enable host key checking and
`StrictHostKeyChecking=yes`; repository legacy defaults disable this and must not
be used. Run inventory graph/host validation and ping before execution. The role
uses only ansible-core modules and Python standard library. Syntax validation was
performed with installed ansible-core 2.21.3/Python 3.14; Python 3.15 with
ansible-core 2.19.3 failed before loading the playbook and is not a supported test
runtime for this change.

Output is a sanitized `v5-readonly-observation/v1` object, NOT a dispatch receipt.
Ansible saves it before failing incomplete acceptance. An unexpected transport
failure produces no observation and must block the caller. Check mode is not
runtime evidence. Ansible stages its script in its managed temporary directory;
no application/configuration write is performed. Rollback requires no service
change; retain evidence and remove only owned temporary staging files normally.

V5-06 verifies exact schema and clean migration state; V5-08 verifies subscription
references. V5-07 remains PARTIAL because counts do not prove equality. V5-10
remains PARTIAL because positive bytes do not prove each account or complete
Exporter→Vector→Billing→PostgreSQL→Accounts chain. V5-11 requires the exact declared
Agent population and fresh heartbeats for all of it. V5-13 checks source SHA and
all discovered Flux/Helm/ESO readiness, but remains PARTIAL until semantic drift
verification. ESO lacks observedGeneration in the installed API; Ready and
refreshTime cannot establish current-spec equivalence. All other suites remain
NOT_RUN. Overall result therefore stays FAIL, never a successful no-op.

After review and UAT, migration dispatch may call this same role in a job matrix.
The adapter must bind actual owner SHA, host/CMDB identity, environment, parent and
child run/attempt, artifact identity, and required checks using Toolkit's offline
receipt verifier. Do not wrap this observation as a successful receipt or invent
GitHub run IDs for a manual SSH execution.
