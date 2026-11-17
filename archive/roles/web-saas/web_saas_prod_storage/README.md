# Canonical PROD Web SaaS storage

The host bootstrap reads the caller's IaC CMDB and accepts only
`open-platform-prod / asia-east1-a / web-saas-prod-data`, device
`google-web-saas-prod-data`, mount `/data` and host `web-saas-prod`.
No provider API, Terraform state or CMDB mutation is performed.

The existing data-volume role initializes only a blank disk, refuses other
mounts/signatures/partitions/nonempty mountpoints, and mounts by filesystem UUID.
An interrupted format can resume only on the owner label `ws-prod-data`.
Subsequent deploys validate the same disk/mount and retain its data.
An existing PostgreSQL container must already bind `/data/postgresql`;
moving an old named volume requires a separately verified migration.

The host bootstrap then selects the GitOps `.doco-cd.prod.yml` declaration
using Doco-CD's [poll target](https://doco.cd/latest/Poll-Settings/).
Merge the GitOps environment/Compose declaration before using this PROD owner.

This host preparation does not enable PROD schema initialization, alter the
stable Accounts/Billing API routing, prove full-business copy equality, or
grant a PROD Full upgrade qualification. Those controls remain independent.
