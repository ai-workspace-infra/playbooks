# Common vhost configuration

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
and cluster roles. Its final receipt must include `COMMON_NODE_VERIFIED`.
The full legacy common baseline remains an explicit separate caller choice;
this profile does not change firewall rules, credentials or business services.

The owner action supports `phase=common` for infrastructure-only creation. It
runs this common profile and emits its bound receipt, while disk initialization
is disabled and storage/K3s roles are skipped. `phase=cluster` continues those
roles only after the same common checks. V6 callers must select common after
an IaC apply; plan remains read-only without host preparation.
