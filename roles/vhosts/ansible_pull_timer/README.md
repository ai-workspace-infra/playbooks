# ansible-pull timer

Single responsibility: install a systemd timer that keeps a pull-mode
convergence entrypoint applied on a host. Defaults converge the Flux controller
resource bounds through `pull_flux_controller_limits.yml`; the host pulls the
repository itself, so the timer keeps working without a controller-driven run.

Requires systemd and, in practice, root: the scheduled pull reads the admin
kubeconfig from `k3s_platform_kubeconfig_path`
(default `/etc/rancher/k3s/k3s.yaml`). Does not install Flux, K3s or Helm. When
`ansible_pull_timer_manage_prerequisites` is true it ensures `git` exists and
installs `ansible-core` if `ansible-pull` is missing.

Inputs (see [defaults](defaults/main.yml)):

| Variable | Default | Purpose |
| --- | --- | --- |
| `ansible_pull_timer_name` | `ansible-pull-flux-limits` | unit name prefix |
| `ansible_pull_repo_url` | playbooks repository | git source for the pull |
| `ansible_pull_checkout` | `main` | branch/tag/commit to check out |
| `ansible_pull_playbook` | `pull_flux_controller_limits.yml` | playbook to converge |
| `ansible_pull_checkout_dir` | `/var/lib/ansible-pull/playbooks` | persistent checkout |
| `ansible_pull_inventory` | `localhost,` | inventory override for pull mode |
| `ansible_pull_schedule` | `*-*-* 00/6:17:00` | `OnCalendar` expression |
| `ansible_pull_timer_manage_prerequisites` | `true` | install git/ansible-core |

```yaml
- hosts: k3s
  become: true
  roles:
    - vhosts/ansible_pull_timer
```

The `k3s_platform_bootstrap` coordinator imports this role after the identity
phase when `ansible_pull_timer_enabled` is true, so a bootstrapped node gets the
timer without an extra run. The standalone
[ansible-pull-timer.yml](../../../ansible-pull-timer.yml) entrypoint applies it
to existing hosts.

Completion requires `systemctl list-timers` to report the timer unit. Check runs
with `journalctl -u <name>.service`. To pause the schedule set
`ansible_pull_timer_state: stopped`; to remove it, stop and disable the timer and
delete the two unit files. Rolling back a resource-bound change means reverting
the playbook inputs, not deleting the timer.
