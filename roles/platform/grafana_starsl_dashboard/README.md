# StarsL K8s Dashboard import

This controller-side role renders a checksum-pinned upstream Dashboard for
Playbooks file provisioning and the existing GitOps Grafana provider. It does
not contact a cluster, change a Grafana database, or deploy exporters. Its only
write capability is the explicitly selected local output when apply is true.

## Source and licence

- Upstream: `starsliao/Prometheus`, Apache-2.0 (see `files/LICENSE.upstream`).
- Commit: `16a813d8c4144397553a89e99453cc98a5709cc6`.
- Path: `kubernetes/K8S Dashboard CN 20240513 StarsL.cn-1715536276053.json`.
- Raw SHA-256: `c33eb5bd786802c2bd58e2f180e8008c468b1e9b79c5fa8887ac7680c363fba4`.
- Original UID: `StarsL_cn_K8S`; retained without overwriting another dashboard.
- `files/upstream.json` is the unmodified input; generated output is
  `roles/docker/observability-server/files/starsl-k8s-dashboard.json`.

## Local rendering

```sh
ansible-playbook -i localhost, import_starsl_k8s_dashboard.yml \
  -e starsl_dashboard_gitops_root=/absolute/path/to/gitops-topic-worktree
# Review the plan, then write only the reviewed controller-side outputs:
ansible-playbook -i localhost, import_starsl_k8s_dashboard.yml \
  -e starsl_dashboard_gitops_root=/absolute/path/to/gitops-topic-worktree \
  -e starsl_dashboard_apply=true
```

`starsl_dashboard_apply` defaults false. `--check` also suppresses writes.
`starsl_dashboard_output_file` can select another absolute controller file;
`starsl_dashboard_gitops_root` defaults empty and skips GitOps generation.
The renderer is also callable directly with `--output-dashboard`,
`--gitops-root`, and optional `--apply`. A source checksum mismatch fails closed.
Runtime authentication is unnecessary for these local rendering operations.

The generated GitOps ConfigMap is added to the existing `import-general`
projected provider. Existing 17 Dashboards and their ConfigMaps remain intact.
Grafana's provider checksum changes deterministically so Helm can roll the
configuration through the existing release path. Rendering again is a no-op.
Review both repositories' PRs; no direct API import bypasses GitOps.
If the older source-host configuration migration renderer is replayed, run this
role afterwards to restore this independently sourced addition; its upstream
input is not part of the old source-host snapshot inventory.

## Data contract and adaptations

The default datasource variable selects `victoriametrics`, but users can choose
any Prometheus-compatible datasource. The Dashboard selects `cluster` and
`environment`, then node, namespace, container and Pod. No cluster name or
remote-write destination is embedded, so a central store can retain origin
clusters. Every metric selector is scoped to the selected cluster/environment.

- kube-state-metrics: `kube_node_info`, `kube_node_spec_taint`,
  `kube_node_status_allocatable`, `kube_node_status_condition`,
  `kube_namespace_created`, `kube_pod_container_info`,
  `kube_pod_container_resource_requests`, `kube_pod_container_resource_limits`,
  `kube_pod_container_status_restarts_total`, `kube_pod_created`, `kube_pod_info`,
  `kube_pod_spec_volumes_persistentvolumeclaims_info`,
  `kube_{deployment,daemonset,statefulset}_metadata_generation`,
  `kube_service_info`.
- kubelet `/metrics/cadvisor`: `container_cpu_usage_seconds_total`,
  `container_memory_rss`, `container_memory_working_set_bytes`,
  `container_network_{receive,transmit}_bytes_total`,
  `container_fs_{usage,limit}_bytes`, with genuine `node`, `namespace`, `pod`,
  `container` labels. `id="/"` node metrics remain dependent on kubelet output.
- kubelet `/metrics`: `kubelet_volume_stats_{available,used}_bytes` plus
  `namespace` and `persistentvolumeclaim`. These are optional for volume
  plugins that do not expose volume statistics; absence is not a healthy zero.

Raw `container_*` selectors use the canonical `job="cadvisor"`; this is the
kubelet's existing `/metrics/cadvisor` endpoint, not a separately installed
cAdvisor service. Ten application CPU/working-set aggregations that previously
only excluded empty container labels also exclude `container="POD"`. Pod network
selectors retain sandbox samples, and the `id="/"` CPU totals keep their explicit
host-cgroup meaning. This prevents pause/container ambiguity without changing
network or host-total semantics.

There are no recording-rule dependencies. The upstream `origin_prometheus`
label is mapped to cluster/environment; bare Pod-info joins are scoped and
Pod joins include namespace. Docker-only `name`/`image` restrictions are removed
for containerd. CPU/memory limit denominators use kube-state-metrics resource
limits rather than optional cAdvisor spec metrics. Declared limits may be
absent for unbounded workloads; the Dashboard then reports no ratio.

Secret and ConfigMap inventory queries are omitted; the namespace table
explains this choice. The exporter needs no permission to read either resource
for this Dashboard. The unrelated hidden Cassandra heap query is removed.
Missing values display `No data`, not zero. Node/process exporter metrics are
served by the existing dedicated Dashboards; this upstream K8s Dashboard uses
kube-state-metrics and kubelet data, and cannot prove those other exporters.

## Verification and rollback

Validate with `python -m unittest discover -s tests -p
'test_starsl_k8s_dashboard.py'`, Ansible syntax-check, deterministic replay and
the GitOps overlay render. After an approved deployment use the dedicated
read-only Grafana identity to verify the datasource, variable choices and real
queries in at least two isolated cluster/environment scopes. A rendered
ConfigMap, successful Flux reconcile or empty query response does not prove
fresh samples. Record optional PVC metrics explicitly as not applicable when
unsupported; do not create synthetic values.

Rollback uses reviewed GitOps/Playbooks revert PRs to remove this Dashboard and
its provider reference; retain the existing dashboards. No resource, database,
credential, history, DNS or exporter rollback is part of this role.

## Conditional panels

Panel 88's untainted-node count subtracts distinct tainted nodes from actual
inventory, using that inventory alone for the no-taint zero baseline. Multiple
taints on one node count once; missing inventory stays absent. Taint details
remain empty without matching taints. Namespace CPU >0.5 core and WSS >1 GiB
panels intentionally show only threshold exceedances. Panels 47 J/S and 87 J/M
retain real per-container filesystem queries and explain unavailable selected
k3s/containerd series; root/node disk statistics and zero values do not replace
missing container telemetry. No collector privileges change.
