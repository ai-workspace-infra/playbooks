# Scoped host Dashboard provisioning

Offline controller-only owner for the existing `StarsL-JOB-node` and
`process-exporter-with-tree` Dashboards. Adds cluster/environment variables and
scopes each Node/process metric selector to those dimensions and the canonical
`node-exporter` / `process-exporter` job. Node's existing job selector is retained
as a single canonical-source choice. No credentials, target inventory, HTTP,
service reload or deployment is used. Requires Python 3 and PyYAML.

Process panels 11/12/13 retain their IDs and explicitly explain that IO/FD metrics
are deliberately dropped by the non-root agent profile. They query nothing and
show no fake zero. PSS panels retain real queries with visible permission limits;
resident memory and CPU success do not establish PSS or complete process coverage.
No read permissions are expanded. Other panel IDs and user Dashboards stay intact.

Inputs: `host_dashboard_scope_playbooks_root` defaults to this checkout;
`host_dashboard_scope_gitops_root` optionally selects an existing GitOps worktree;
`host_dashboard_scope_manifest` optionally selects an output YAML path;
`host_dashboard_scope_apply` defaults false. Paths must be absolute. Check mode
always plans. Example (all local; no CMDB/host delivery):

```sh
ansible-playbook -i 'localhost,' scope_host_grafana_dashboards.yml --check
python3 roles/platform/grafana_host_dashboard_scope/files/render_dashboards.py \
  --playbooks-root /absolute/playbooks-worktree \
  --gitops-root /absolute/gitops-worktree \
  --manifest /absolute/runtime/query-manifest.yaml --apply
```

The script adapts each existing source separately, preserving consumer-only
metadata. It updates only two legacy JSON models, the two matching embedded
models in GitOps `source-grafana-general-0.yaml`, and the deterministic bundle
checksum. The historical import inventory remains archival and is not rewritten.
After replaying the legacy source-host importer, replay StarsL's importer first,
then this scope adapter. Both producers hash the actual final ConfigMap data.
Review their diffs before any separate GitOps release/promotion; rendering does
not prove provisioning or data acceptance. Roll back through reviewed source PRs
and a new release; do not mutate published tags. This change is excluded from
the concurrent incident-recovery release.

## Viewer acceptance manifest

`--manifest` enumerates every query variable in dependency order, every target
(including hidden), exact model hashes, and complete metric-family sets for
StarsL K8s, Node and Process. With `--gitops-root`, it uses actual consumer models.
It is a query plan, not an executable query API or PASS evidence: Grafana macro
expansion, datasource UID selection, externally verified expected nodes, actual
semantic queries/joins and continuity must be checked by the existing independent
Viewer owner in its in-memory token lifecycle. Do not create another token or
use administrator query fallback. Keep output sanitized and bounded.

Legitimate zero values remain values. Empty metrics need applicability evidence:
no taints, no configured limits/requests, no mounted PVC or unsupported volume
stats are conditional, not unconditional health. Container filesystem queries
require compatible `id`/`device` labels. Partial process scrape errors and PSS
visibility require explicit inspection. The current representative acceptance
helper does not alone cover the full family/variable/target manifest.

Node panel 200 derives P99 CPU/memory percentages from scoped raw metrics with
`[7d:5m]` subqueries, preserving the instance join and requiring no recording
rules. A seven-day window samples only available history; recent data does not
prove seven-day continuity. The pinned disposable local VictoriaMetrics fixture
verifies percentages and new-node behavior without production access.
