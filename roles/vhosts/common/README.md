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
