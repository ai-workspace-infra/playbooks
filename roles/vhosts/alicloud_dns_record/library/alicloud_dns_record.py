#!/usr/bin/python
# -*- coding: utf-8 -*-

from ansible.module_utils.basic import AnsibleModule

from alibabacloud_alidns20150109.client import Client as Alidns20150109Client
from alibabacloud_credentials.client import Client as CredentialClient
from alibabacloud_tea_openapi import models as open_api_models
from alibabacloud_tea_util import models as util_models
from alibabacloud_alidns20150109 import models as alidns_models


# Build Client (AK/SK 优先 → STS → Credential Chain)
def create_client(access_key_id=None, access_key_secret=None, security_token=None):
    if access_key_id and access_key_secret:
        config = open_api_models.Config(
            access_key_id=access_key_id,
            access_key_secret=access_key_secret,
            security_token=security_token
        )
        config.endpoint = "alidns.aliyuncs.com"
        return Alidns20150109Client(config)

    credential = CredentialClient()
    config = open_api_models.Config(credential=credential)
    config.endpoint = "alidns.aliyuncs.com"
    return Alidns20150109Client(config)


# Helper: find existing record
def find_record(client, domain, rr, record_type):
    req = alidns_models.DescribeDomainRecordsRequest(
        domain_name=domain,
        rr_key_word=rr,
        type_key_word=record_type,
        page_size=100
    )
    resp = client.describe_domain_records_with_options(
        req, util_models.RuntimeOptions()
    )
    records = resp.body.domain_records.record or []

    for r in records:
        if r.rr == rr and r.type == record_type:
            return r

    return None


def main():
    module = AnsibleModule(
        argument_spec=dict(
            state=dict(type='str', choices=['present', 'absent'], default='present'),
            domain=dict(type='str', required=True),
            rr=dict(type='str', required=True),
            type=dict(type='str', required=True),
            value=dict(type='str'),
            ttl=dict(type='int', default=600),
            priority=dict(type='int'),

            # 支持 AK/SK
            access_key_id=dict(type='str', no_log=True),
            access_key_secret=dict(type='str', no_log=True),
            security_token=dict(type='str', no_log=True),
        ),
        supports_check_mode=True
    )

    state = module.params["state"]
    domain = module.params["domain"]
    rr = module.params["rr"]
    record_type = module.params["type"]
    value = module.params["value"]
    ttl = module.params["ttl"]
    priority = module.params["priority"]

    access_key_id = module.params["access_key_id"]
    access_key_secret = module.params["access_key_secret"]
    security_token = module.params["security_token"]

    client = create_client(access_key_id, access_key_secret, security_token)

    # Find record
    try:
        existing = find_record(client, domain, rr, record_type)
    except Exception as e:
        module.fail_json(msg=f"Failed to query DNS records: {e}")

    # ----------------------------
    # ABSENT (delete)
    # ----------------------------
    if state == "absent":
        if not existing:
            module.exit_json(changed=False, msg="Record already absent")

        if module.check_mode:
            module.exit_json(changed=True)

        try:
            req = alidns_models.DeleteDomainRecordRequest(
                record_id=existing.record_id
            )
            client.delete_domain_record_with_options(req, util_models.RuntimeOptions())
        except Exception as e:
            module.fail_json(msg=f"Failed to delete record: {e}")

        module.exit_json(changed=True, msg="Record deleted", record_id=existing.record_id)

    # ----------------------------
    # PRESENT (create / update)
    # ----------------------------
    if not value:
        module.fail_json(msg="value is required when state=present")

    if existing:
        need_update = (
            existing.value != value or
            existing.ttl != ttl or
            (priority is not None and existing.priority != priority)
        )

        if not need_update:
            module.exit_json(changed=False, msg="Record already up to date", record_id=existing.record_id)

        if module.check_mode:
            module.exit_json(changed=True)

        try:
            req = alidns_models.UpdateDomainRecordRequest(
                record_id=existing.record_id,
                rr=rr,
                type=record_type,
                value=value,
                ttl=ttl,
                priority=priority,
            )
            client.update_domain_record_with_options(req, util_models.RuntimeOptions())
        except Exception as e:
            module.fail_json(msg=f"Failed to update record: {e}")

        module.exit_json(changed=True, msg="Record updated", record_id=existing.record_id)

    # ----------------------------
    # CREATE
    # ----------------------------
    if module.check_mode:
        module.exit_json(changed=True)

    try:
        req = alidns_models.AddDomainRecordRequest(
            domain_name=domain,
            rr=rr,
            type=record_type,
            value=value,
            ttl=ttl,
            priority=priority,
        )
        resp = client.add_domain_record_with_options(req, util_models.RuntimeOptions())
        record_id = resp.body.record_id
    except Exception as e:
        module.fail_json(msg=f"Failed to create record: {e}")

    module.exit_json(changed=True, msg="Record created", record_id=record_id)


if __name__ == "__main__":
    main()
