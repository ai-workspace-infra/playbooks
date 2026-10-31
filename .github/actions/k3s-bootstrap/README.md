# K3s bootstrap owner action

Runs the existing Playbooks `scripts/pipeline/bootstrap-k3s.sh` entry for one
CMDB host. The caller supplies a clean GitOps checkout and its resolved SHA,
the IaC CMDB artifact and checksum, a resource declaration relative to the
checkout, and a fresh per-host receipt path. Set up Ansible, SSH access and
the independently scoped Vault reader/writer identities before invoking it;
credentials remain in the runner environment and are not action inputs or
outputs.

The owner entry checks the GitOps commit, clean checkout, CMDB checksum,
manifest containment and host presence; it runs the common and K3s roles and
validates three receipts before this action succeeds. The caller publishes and
correlates the three output paths per host, and distinguishes runtime service
acceptance from Flux object readiness. This action does not create cloud
resources, modify DNS or perform data migration. Disk initialization follows
the reviewed declaration and role safety checks.
