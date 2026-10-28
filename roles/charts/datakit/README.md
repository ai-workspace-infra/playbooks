# DataKit Helm role

Manage the DataKit Helm release in a Kubernetes DEV/SIT/UAT cluster. This role enables the built-in `prom` collector with explicit Caddy and PostgreSQL exporter URLs, and limits stdout/stderr log collection to the configured business namespace selector. It does not expose DataKit's own API or change production.

## Inputs

- `target_env`: required; one of `dev`, `sit`, or `uat`.
- `k8s_cluster_name`: optional cluster tag.
- `datakit_business_log_namespace_selector`: defaults to `namespace:feature-*` for the current DEV feature lanes.
- `datakit_caddy_metrics_url`: defaults to the current SIT Caddy metrics Service URL on port `9180`.
- `datakit_postgresql_metrics_url`: defaults to the current SIT PostgreSQL exporter Service URL on port `9187`; retained from the existing live DataKit configuration.
- `datakit_dataway_url` or Vault credentials and `datakit_vault_path`: required to configure the DataWay secret. Secret values are hidden from task output.
- Helm chart, release, namespace, version, and resource budget variables are in `defaults/main.yml`.

## Target and dependencies

Run from the `k8s_masters` group resolved by `inventory/terraform_cmdb.py`. Requires `kubernetes.core`, `community.hashi_vault`, Helm, Kubernetes API access, and a DataWay URL provided at runtime or from Vault.

## Example

```bash
ansible-playbook -i inventory/terraform_cmdb.py deploy_datakit.yml \
  -e target_env=dev -e k8s_cluster_name=dev-k3s-v5 --syntax-check
```

Use the same CMDB inventory for a reviewed DEV/SIT/UAT deployment. The role asserts the environment before making Kubernetes changes; `prod` is rejected. `target_env` should match the target workload lane, and service URLs must be overridden through inventory/group variables when the non-production lane differs from these current SIT defaults.

## Rollback

Re-run the role with the prior reviewed values and chart version to restore the previous Helm revision. To stop only Caddy metric collection, remove the Caddy `dkconfig` entry and retain the PostgreSQL exporter entry. To restore the prior broad container log behavior, set `datakit_business_log_namespace_selector` to the previously approved selector. Rollback and deployment must use the same non-production cluster target.
