# K3s control-plane initialization

Single responsibility: initialize a declared K3s server and verify its API.
This is the shared control-plane installer used by platform bootstrap and the
manual cluster-server entrypoints. The former `k3s-cluster` script installer
has been retired; no compatibility role or extra installer is retained.

## Contract

Target group: `k3s`, using IaC CMDB inventory. Run as the system administrator.
Run `vhosts/common` first: hostname and OS accounts remain its responsibility.
The role checks the OS hostname instead of renaming a running K3s/PV node.
When `k3s_platform_require_data_mount=true`, the data-volume role must supply
`web_saas_data_evidence.filesystem_uuid`; K3s is bound to that verified mount.

| Input | Purpose |
| --- | --- |
| `k3s_platform_node_name` | Canonical node identity; defaults to inventory hostname |
| `k3s_platform_k3s_version` | Pinned initial installation version |
| `k3s_platform_k3s_config_path`, `k3s_platform_install_dir` | Config and data paths |
| `k3s_platform_cluster_cidr`, `k3s_platform_service_cidr` | Cluster networking |
| `k3s_platform_tls_sans`, `k3s_platform_disable_components` | Declared API names and built-in component selection |
| `k3s_platform_write_kubeconfig_mode` | Must be `0600`; no business/audit access to admin credentials |
| `k3s_platform_require_data_mount`, `k3s_platform_local_storage_path` | Independent filesystem dependency |

Full defaults: [defaults/main.yml](defaults/main.yml). Inputs are non-secret
configuration from GitOps; node facts come from CMDB. The role installs no
Helm, Flux, ESO, Registry, business Secret or DNS resources. CA key material
remains on the master. Successful completion requires `/readyz` success.

```yaml
- hosts: k3s
  become: true
  roles:
    # Run common and the declared filesystem preparation before this play.
    - vhosts/k3s-cluster-server
```

## Retry and rollback

An existing binary/unit skips initial installation. This is not a version
upgrade implementation. Config and service state are converged; data is not
removed or reformatted. `action` values other than `bootstrap` are rejected:
the former add-master/backup/recovery/upgrade/destroy task files were empty,
and must not report successful operations. Use separately reviewed owners for
these capabilities. No automatic downgrade, CA rotation or cleanup is provided.

The retired `k3s` installer also installed Helm and carried an old kube-ovn
script that could delete running pods. Helm has its own role; third-party CNI
installation requires a separately reviewed GitOps owner. This role explicitly
rejects legacy non-default `cni` inputs instead of silently deploying Flannel.

The old implicit local Registry/nerdctl/self-signed CA setup is intentionally
removed from the two legacy install playbooks; Registry provisioning is a
separate operation. The old role-generation script and static server vars
are removed so they cannot recreate the retired implementation.
