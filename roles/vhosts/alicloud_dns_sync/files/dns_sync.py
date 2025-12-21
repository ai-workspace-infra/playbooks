#!/usr/bin/env python3
import sys
import yaml
from alibabacloud_alidns20150109.client import Client
from alibabacloud_tea_openapi import models as open_api_models


def client(ak, sk):
    config = open_api_models.Config(
        access_key_id=ak,
        access_key_secret=sk,
        endpoint="alidns.aliyuncs.com",
    )
    return Client(config)


def sync(domain, records, ak, sk):
    c = client(ak, sk)

    # get all existing records
    resp = c.describe_domain_records(
        open_api_models.Config(domain_name=domain)
    )
    existing = { (i.rr, i.type): i for i in resp.body.domain_records.record }

    for rec in records:
        key = (rec["rr"], rec["type"])
        ttl = rec.get("ttl", 600)

        if key not in existing:
            print("CREATE:", rec)
            c.add_domain_record({
                "DomainName": domain,
                "RR": rec["rr"],
                "Type": rec["type"],
                "Value": rec["value"],
                "TTL": ttl,
            })
        else:
            cur = existing[key]
            if cur.value != rec["value"] or cur.ttl != ttl:
                print("UPDATE:", rec)
                c.update_domain_record({
                    "RecordId": cur.record_id,
                    "RR": rec["rr"],
                    "Type": rec["type"],
                    "Value": rec["value"],
                    "TTL": ttl,
                })


if __name__ == "__main__":
    fn = sys.argv[1]
    ak = sys.argv[2]
    sk = sys.argv[3]

    cfg = yaml.safe_load(open(fn))
    for domain, recs in cfg.items():
        sync(domain, recs, ak, sk)
