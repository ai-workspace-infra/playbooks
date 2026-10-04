# Persistent web-saas data volume

The playbook `web-saas-data-volume.yml` runs this reusable role on one exact
CMDB-selected web-saas host. `inspect` is read-only. `prepare_empty` may format
an unformatted disk only when the caller explicitly sets
`web_saas_data_allow_initialize=true`; it refuses any nonempty or already
mounted `/data` directory. Existing database files must be backed up, quiesced,
copied and compared through a separate reviewed migration procedure before
switching mounts. This role never moves, deletes or overwrites business data.

Required caller inputs: `web_saas_data_target_host`,
`web_saas_data_environment` (`uat` or `prod`), `web_saas_data_device_path`
(stable `/dev/disk/by-id/...` path), and `web_saas_data_action`. The controller
must authenticate the GitOps/CMDB artifact and disk identity; a caller-provided
boolean does not prove that a disk belongs to the expected environment.

Run `ansible-playbook --syntax-check -i 'web-saas-uat,'
web-saas-data-volume.yml -e 'web_saas_data_target_host=web-saas-uat'` after
editing the role. Syntax success is not proof that a remote disk is mounted.
