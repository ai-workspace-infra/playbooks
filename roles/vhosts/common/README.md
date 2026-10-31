# Common vhost configuration

## Explicit OS identity profile

`tasks_from: os_identity.yml` reads the `authorized_keys` fields from Vault KV v2
`<environment>/security/{root,operator,auditor}` on the controller and configures
the three OS accounts. Invoke `common_os_identities.yml` with an explicit
`common_os_identity_environment` (`sit`, `uat`, or `prod`) and `VAULT_ADDR`;
the controller needs `vault`, `ssh-keygen`, Vault authentication and the
`ansible.posix` collection. The profile fails on unmanaged user, group or home
collisions, missing public keys, or incompatible SSH authentication policy.

It uses the `operator` and `auditor` groups as their respective primary groups.
An existing empty `operator` group may be reused; unexpected members, unmanaged
accounts and home collisions fail before changes. Reused OS group ownership and
permissions must be inspected on the target before enabling this profile. It adds
authorized keys without deleting existing keys, preserves root, and uses
root-owned markers for safe reruns. The initial
operator sudo rule permits only `systemctl is-system-running`; other runtime and
cluster permissions belong to their owners and are not implicitly granted. The
auditor gets no sudo or supplementary groups. This profile checks bounded OS
allow/deny conditions; actual SSH login and broader filesystem/ACL access still
need independent verification. Shared keys allow their holder to log in as each
authorized role and do not provide per-person separation.

The `operator` account receives `/bin/bash` at creation and is checked for a
working login shell. This is the OS-side prerequisite for running ordinary
commands such as `kubectl` after the K3s role installs/provides its client.
K3s authentication, the operator kubeconfig and Kubernetes RBAC belong to the
K3s identity role. Do not add `operator` to `docker`, `containerd` or other
runtime-admin groups, and do not grant access to the root K3s kubeconfig or
container runtime sockets. This common profile does not install the K3s client
or assign Kubernetes permissions.

This profile is invoked explicitly through `common_os_identities.yml`; it does
not implicitly run as part of `node_bootstrap.yml`. The K3s caller must run the
OS identity profile before the K3s identity role. Rollback restores only the
selected new sudo policy and role-managed accounts/keys after checking
consumers and preserving existing root access; do not delete old audit
accounts or persistent data as a side effect.

Owner of shared OS configuration and the controller prerequisites used to parse
vhost roles. Remote bootstrap remains explicitly selected by each caller; preparing
the controller does not enable common host, firewall or SSH changes.

Before invoking Ansible, provision the shared, pinned controller collections:

```sh
export ANSIBLE_COLLECTIONS_PATH=/absolute/private/runtime/collections
export ANSIBLE_COLLECTIONS_SCAN_SYS_PATH=False
bash roles/vhosts/common/files/prepare_controller.sh
```

`controller-collections.yml` is the single dependency source for shared vhost
execution. The helper runs locally with an existing `ansible-galaxy`; it does not
install Python, read Vault, contact managed hosts or change cloud resources.
The explicit private directory prevents globally installed collections from
masking missing dependencies. Reruns reuse already installed matching versions.
Callers must retain the same collection path when invoking Ansible.

Controller dependencies must exist before role parsing: a skipped Vault task
still requires `community.hashi_vault`, and skipped common firewall bootstrap
still requires `ansible.posix`. The control-plane preflight statically imports
the selected vhost roles before inventory or SSH. Failure stops deployment.

Rollback selects the previous immutable Playbooks revision and its corresponding
private controller runtime. Do not replace global collections or remove runtime
evidence to roll back a controller dependency change.

## Fresh K3s node profile

`tasks_from: node_bootstrap.yml` is the explicit common profile for a receipt-bound
fresh K3s generation. It verifies canonical CMDB/inventory identity, checks any
existing K3s node and PV affinity before renaming, synchronizes runtime/static
hostname and local resolution, and persists cloud-init hostname ownership.
`common_node_name` defaults to the declared K3s node name. Any mismatch fails
before disk preparation or K3s installation; existing PVs are never rebound.

The V6 entry invokes this profile automatically after IaC, before the data-volume
and cluster roles. Its receipt includes `COMMON_NODE_VERIFIED` and
`OS_IDENTITY_CHECKS_PASSED`; separately verify actual SSH logins and the
remaining host security baseline before declaring acceptance.
The full legacy common baseline remains an explicit separate caller choice;
this profile does not change firewall rules, credentials or business services.

The owner action supports `phase=common` for infrastructure-only creation. It
runs this common profile and emits its bound receipt, while disk initialization
is disabled and storage/K3s roles are skipped. `phase=cluster` continues those
roles only after the same common checks. V6 callers must select common after
an IaC apply; plan remains read-only without host preparation.
