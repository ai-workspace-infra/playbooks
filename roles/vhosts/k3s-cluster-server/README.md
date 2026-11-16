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
| `k3s_platform_cluster_dns` | CoreDNS Service IP; must belong to the service CIDR |
| `k3s_platform_tls_sans`, `k3s_platform_disable_components` | Declared API names and built-in component selection |
| `k3s_platform_write_kubeconfig_mode` | Must be `0600`; no business/audit access to admin credentials |
| `k3s_platform_require_data_mount`, `k3s_platform_local_storage_path` | Independent filesystem dependency |
| `k3s_platform_datastore`, `k3s_platform_cluster_init` | Datastore backend (`sqlite` default for new single-node clusters; `etcd` optional) |
| `k3s_platform_enable_mem_tuning`, `k3s_platform_gomemlimit`, `k3s_platform_gogc` | Go runtime memory tuning in `/etc/systemd/system/k3s.service.env` (`768MiB` / `50`) |
| `k3s_platform_safe_restart_script_path` | Path to safe memory restart script (`/usr/local/bin/safe-k3s-mem-restart`) |

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

## Supported platforms

The initializer validates the OS family before it installs anything and then
installs the family-specific dependency list from vars/main.yml:

| OS family | Validated target | OpenSSH client package | Notes |
| --- | --- | --- | --- |
| Debian | Debian 13 (trixie) | openssh-client | Reference platform for PROD-K3S-V6 |
| RedHat | Rocky Linux 10.x (RHEL 10 family) | openssh-clients | dnf; no ufw; firewalld handled by vhosts/common |

Anything else fails the platform assertion instead of installing a guessed
package set. Adding a family means adding its dependency names and a contract
test, not loosening the assertion.

The K3s data path has two declared modes (k3s_platform_data_mode):

- volume (default): k3s_platform_require_data_mount=true, an independent
  filesystem is mounted at /data and its UUID is asserted.
- directory: /data is an allocated root-owned directory on the system disk.
  The bootstrap creates it, K3s still uses /data/k3s/storage, and the mount
  assertion is skipped by design. Capacity and root-disk growth must be
  reviewed by the change that selects this mode.

## Memory tuning and safe memory restart

The role renders `/etc/systemd/system/k3s.service.env` (when `k3s_platform_enable_mem_tuning=true`) with `GOMEMLIMIT` (`k3s_platform_gomemlimit`, default `768MiB`) and `GOGC` (`k3s_platform_gogc`, default `50`) to bound the K3s Go runtime heap on memory-constrained control-plane nodes.

It also installs `/usr/local/bin/safe-k3s-mem-restart` (`k3s_platform_safe_restart_script_path`), which implements the 5-step safe memory restart workflow without terminating running workload containers (`KillMode=process`):

1. **Pre-restart snapshot & baseline capture**: Saves an on-demand `k3s etcd-snapshot` and records baseline node, `Kustomization`, and `HelmRelease` health.
2. **Validate & reload systemd environment**: Ensures `/etc/systemd/system/k3s.service.env` contains `GOMEMLIMIT` and `GOGC`, then runs `systemctl daemon-reload`.
3. **Restart `k3s.service`**: Restarts only the `k3s server` process while keeping `containerd-shim` workload pods running.
4. **Wait for API readiness**: Polls `kubectl get --raw=/readyz` until the control plane is ready.
5. **Post-restart verification**: Verifies node readiness, pod states, Flux `Kustomization`/`HelmRelease` status, APIServer Go runtime metrics (`go_goroutines`), and net reclaimed physical RSS memory.
