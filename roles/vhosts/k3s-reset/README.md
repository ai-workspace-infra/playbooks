# K3s reset

This role operates on the explicitly selected host group as root. It checks K3s
binaries, service units and installation/configuration paths before invoking the
legacy reset script. A conclusively empty node returns success without change or
network cleanup; an indeterminate inspection fails. The role does not treat a
retained independent data disk as permission to delete or reformat it.

The existing reset script for a detected installation still downloads and runs
Kube-OVN cleanup, removes K3s directories and changes host network state. Review
its effects, persistent-volume data and recovery plan before use; this empty-node
fix does not make the existing destructive path safe for arbitrary clusters.
The standalone playbook still requires a reviewed inventory and explicit target
limit and group. Its legacy `cluster_reset` default is unchanged by this fix.

```bash
ansible-playbook -i inventory/terraform_cmdb.py k3s_reset.yml \
  --limit '<selected-node>' -e group=k3s --diff
```

A no-op result is not proof that a prior cluster's data or external resources
were removed. Reverting this change restores the old reset behavior; it does not
restore a deleted cluster or data. The detected-installation path has not been
changed or runtime-validated by this no-op correction.
