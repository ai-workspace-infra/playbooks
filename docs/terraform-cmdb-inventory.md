# Terraform CMDB dynamic inventory

Use the CMDB artifact emitted by the IaC provider module:

```sh
AI_WORKSPACE_CMDB_JSON=/path/to/cmdb.json \
  ansible-inventory -i inventory/terraform_cmdb.py --list
```

`inventory/terraform_cmdb.py` accepts the canonical `cmdb.v1` envelope and
keeps compatibility with the previous flat host map. The canonical host
records are under `hosts`; each record must provide a runtime `ip` or
`ansible_host`, an Ansible group list, and provider/resource facts when the
provider exposes them.

The file is an apply artifact, not Terraform state. IaC must regenerate it
after a successful apply from the same GitOps resource declaration and the
same environment. It contains no credentials, private keys, passwords, or
database contents.
