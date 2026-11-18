# Scoped Flux Source operator

This owner plans or promotes one existing production GitRepository and waits for Flux. It does not install K3s/Flux/ESO, create Secrets, alter workloads directly, manage DNS, migrate a database, move PVCs or dispatch a workflow. The operator's existing runtime identity remains the authorization boundary; this role does not grant permissions.

The only supported Source is `platform-config-prod` in `flux-system`, reconciled by root Kustomization `prod-web-saas-k3s-v5` at `./environments/clusters/prod-web-saas-k3s-v5`. Management operations use the verified target host's local K3s kubeconfig with an explicit context. No kubeconfig contents or credential values are returned to the controller.

## Verified controller adapter

Use the adapter from a clean checkout of its reviewed, merged owner SHA. The adapter validates:

- The local Playbooks owner SHA and unchanged owner paths.
- The desired and expected old tags resolve locally and remotely to their declared immutable commits.
- Old→new GitOps changes are limited to `services/platform-observability/prod/**` and the V5 `source.yaml`. A broad current-main snapshot is rejected if it carries unrelated business changes.
- The desired snapshot's Source selects the desired tag and its root path/source relationship matches the chosen cluster.
- One target exists in the authoritative CMDB with the selected instance ID, environment and SSH user; provider/SSH verification was collected within one hour.
- The known-host line is bound to that target address and port. A temporary strict Ansible configuration uses `BatchMode` and `StrictHostKeyChecking=yes`; an existing in-memory SSH agent is used. The adapter never reads or creates a private-key file.

The CMDB must be an IaC artifact. An explicitly authorized, freshly provider-verified temporary inventory can also be supplied in the compatible host-record envelope allowed by repository policy, but must never be presented as an authoritative persisted CMDB. It must retain its verification provenance and remain limited to this operation. Never reuse an old default inventory.

Example plan schema (placeholders are deliberately invalid until replaced with verified facts):

```json
{
  "schema_version": "flux-source-operator-plan/v1",
  "owner_sha": "<reviewed Playbooks commit>",
  "environment": "prod",
  "target": {
    "host": "<selected inventory key>",
    "instance_id": "<provider instance ID>",
    "machine_id": "<verified 32-character machine ID>",
    "ssh_user": "<management or independent audit login>",
    "known_hosts": "<address> ssh-ed25519 <verified public host key>",
    "verified_at": "<UTC provider/SSH verification timestamp>"
  },
  "cluster": {
    "kubeconfig": "/etc/rancher/k3s/k3s.yaml",
    "context": "<verified context>",
    "source_name": "platform-config-prod",
    "source_namespace": "flux-system",
    "kustomization_name": "prod-web-saas-k3s-v5",
    "cluster_root": "./environments/clusters/prod-web-saas-k3s-v5",
    "expected_tag": "<current reviewed tag>",
    "expected_sha": "<current Source artifact commit>",
    "wait_seconds": 600
  },
  "release": {
    "repo_url": "https://github.com/ai-workspace-infra/gitops.git",
    "desired_tag": "<new reviewed tag>",
    "desired_sha": "<new immutable commit>"
  }
}
```

Fetch the reviewed tags into a separate GitOps worktree before running. The adapter does not fetch or create tags and does not modify either checkout.

```sh
# Default: remote reads and a controller-side report; no Source mutation.
python3 scripts/run_gitops_source_promotion.py \
  --plan "$PLAN_JSON" --cmdb "$VERIFIED_CMDB" \
  --gitops-root "$FIXED_GITOPS_WORKTREE" --evidence-dir "$NEW_PLAN_EVIDENCE"

# Only after the exact plan/refs/target and production operation are authorized:
python3 scripts/run_gitops_source_promotion.py \
  --plan "$PLAN_JSON" --cmdb "$VERIFIED_CMDB" \
  --gitops-root "$FIXED_GITOPS_WORKTREE" --evidence-dir "$NEW_APPLY_EVIDENCE" --apply
```

Every run requires a new empty evidence directory. The role's `gitops_promotion_apply` defaults false; Ansible check mode downgrades to a read-only plan. Ansible still uses its own temporary working files and the controller writes evidence; “read-only” refers to target production configuration, not absolute zero filesystem writes.

## Remote checks and mutation boundary

Before mutation, the role checks the actual machine ID, precise Source/root identities, repository, suspension state, expected tag and artifact SHA, ready observed generations and root applied revision. Its only mutation is a JSON patch to that Source's `/spec/ref`; JSON `test` operations bind both the previously read `resourceVersion` and exact old ref. The API server's real identity authorization must permit this action.

The changed Source is observed by Flux normally. No direct chart installation, Kubernetes workload apply or forced reconciliation is used. Success requires Source and root Kustomization generations/Ready conditions and artifact/applied revisions matching the target tag and SHA. Already-selected targets are idempotent and do not receive another patch.

Evidence retains the expected old tag/SHA and desired tag/SHA. A lost patch response is `mutation_failed_or_unknown`, never a preflight success. A timeout or post-mutation read error is non-success and preserves whether mutation was observed. Inspect the exact Source before retrying or choosing a rollback; no automatic rollback is performed.

`verified` proves the Source/root reconciliation only. It explicitly leaves `runtime_metrics_acceptance=NOT_RUN`. Separately verify the agent/stack HelmReleases, actual Pod image IDs, exporter targets, fresh correctly scoped KSM/Node/process/cAdvisor series, continuity, and the eighteenth StarsL Dashboard through approved read identities.

## Dedicated audit status mode

An independent audit SSH login can use `--audit-report` with its own dedicated kubeconfig/context and `target.audit_username` set to the expected Kubernetes username. The target CMDB entry must select this audit login; no `become` is used.

This mode requires a non-root effective UID, inability to read `/etc/rancher/k3s/k3s.yaml`, successful `kubectl auth whoami`, permission to get the exact Source/root and denial of tested Source/Kustomization mutation, Secrets/ConfigMaps, exec, service-account tokens, nodes/proxy, impersonation and RBAC escalation. It only queries Source/root status and permission self-reviews. It does not inspect raw business Secrets, Pod environments or unrestricted logs.

The resulting audit boundary PASS is explicitly scoped to those Kubernetes permissions and management-kubeconfig unreadability; a complete Linux sudo/group/ACL/credential-consumer audit remains `NOT_RUN`. Management/root execution of a plan does not prove audit isolation and reports it as `NOT_RUN`. No audit account, role or credential is created by this owner.

## Rollback

Create a separately reviewed plan with current=new and desired=previous known-good immutable tag/SHA. Set top-level `"rollback": true` and a bounded `"rollback_reason"`, and pass `--rollback`; a marker or CLI option alone is rejected. The controller verifies that desired is a distinct ancestor of expected using the local Git graph after both tags/commits have also matched their remote identities. The observability-only changed-path gate, host/machine/SSH boundary and Source concurrency tests remain unchanged.

```sh
# Recovery plan: reads only; no --apply means no Source change.
python3 scripts/run_gitops_source_promotion.py \
  --plan "$ROLLBACK_PLAN_JSON" --cmdb "$VERIFIED_CMDB" \
  --gitops-root "$FIXED_GITOPS_WORKTREE" --evidence-dir "$NEW_ROLLBACK_PLAN_EVIDENCE" --rollback

# Only with explicit authorization for the exact recovery target:
python3 scripts/run_gitops_source_promotion.py \
  --plan "$ROLLBACK_PLAN_JSON" --cmdb "$VERIFIED_CMDB" \
  --gitops-root "$FIXED_GITOPS_WORKTREE" --evidence-dir "$NEW_ROLLBACK_APPLY_EVIDENCE" --rollback --apply
```

For this explicitly verified rollback only, the root Kustomization may be non-Ready, because a failed Helm health check can leave its last-applied revision on the previous known-good commit. Its last-applied revision must nevertheless be exactly the expected or desired known tag/SHA; unrelated, blank or unknown revisions are rejected. Its source, namespace and root path must still match. The Source itself must be Ready at its exact observed generation and the artifact SHA matching its selected expected/desired ref. Default forward mode still rejects an unhealthy baseline.

Recovery evidence records the direction, reason, controller ancestor verification, baseline Source/root Ready state and their precise revisions. The owner still requires Source/root convergence at the desired revision for success and preserves timeout/unknown-mutation failures. No automatic rollback is introduced.

This selects configuration only: it does not restore data, downgrade storage formats or redirect writers. A rollback across unrelated business changes is rejected and requires its own properly scoped recovery design.

No new CI dispatch or Vault trust allowlist is introduced. An operator may run this owner with authorized runtime credentials, or a separately reviewed control-plane caller may select and invoke it later. Do not widen existing pipeline permissions merely to get a new caller through.


## Explicit forward repair from a known unhealthy root

When the prior configuration itself cannot recover (for example, both the failed release and older rollback package share the same plugin archive-size defect), use a separately reviewed fixed descendant release. This is not a general forward-health bypass. Default forward mode still requires the healthy exact baseline.

The plan must contain top-level `"recover_forward": true` and a non-empty, single-line `"recovery_reason"` of at most 300 characters, and the CLI must also select `--recover-forward`. The CLI/boolean plan marker must match; `--rollback` and `--recover-forward` are mutually exclusive. The controller independently verifies that expected SHA is a strict ancestor of desired SHA **after** both immutable tags resolve to their exact local and remote commits. The same Observability-only plus exact Source path gate, selected host/machine/SSH checks, reviewed owner SHA and bounded evidence rules remain in effect.

```sh
# Explicit repair plan only; does not mutate the Source.
python3 scripts/run_gitops_source_promotion.py \
  --plan "$REPAIR_PLAN_JSON" --cmdb "$VERIFIED_CMDB" \
  --gitops-root "$FIXED_GITOPS_WORKTREE" --evidence-dir "$NEW_REPAIR_PLAN_EVIDENCE" --recover-forward

# Only after the exact repair tag/chart and target are authorized:
python3 scripts/run_gitops_source_promotion.py \
  --plan "$REPAIR_PLAN_JSON" --cmdb "$VERIFIED_CMDB" \
  --gitops-root "$FIXED_GITOPS_WORKTREE" --evidence-dir "$NEW_REPAIR_APPLY_EVIDENCE" --recover-forward --apply
```

For this explicit recovery only, the root may be non-Ready but its `lastAppliedRevision` must exactly match the selected expected or desired tag/SHA. Empty, unrelated and unknown root revisions are rejected. The Source must still be Ready at its exact observed generation and match its selected expected/desired immutable revision. Its repository/ref, root path, namespace and source identity constraints are unchanged. The patch still tests resourceVersion and old ref and only replaces the one Source ref.

Evidence records direction `recover-forward`, the bounded reason, controller ancestry verification/direction and baseline Source/root readiness/revisions. The operation still succeeds only after Source **and** root converge Ready at the desired exact revision; failed/lost writes or convergence timeouts retain non-success/unknown evidence. No automatic rollback, workload patch, data migration, permission expansion or infrastructure change is added. `verified` still does not prove plugin behavior, telemetry, audit isolation or business recovery; those require independent runtime checks.
