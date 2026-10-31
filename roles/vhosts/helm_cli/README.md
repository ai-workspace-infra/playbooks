# Helm CLI

Single responsibility: ensure the Helm client is installed on selected `k3s`
hosts as the system administrator. No Kubernetes API, chart, credentials or
cloud operation is performed. `k3s_platform_helm_version` is the pinned initial
version; see [defaults](defaults/main.yml). Requires curl, CA trust and network
access prepared by the host/control-plane roles.

```yaml
- hosts: k3s
  become: true
  roles:
    - vhosts/helm_cli
```

An existing Helm executable is preserved; this is an installation role, not an
upgrade/rollback mechanism. Completion means the installer succeeded or the
client already existed; it is not service acceptance. Retrying does not deploy
charts. Rollback of the client is a separate reviewed host action.
