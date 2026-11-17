#!/usr/bin/env python3
"""Bounded catalog provider-reference update; never changes subscriber rights."""
import hashlib
import json
import re
import subprocess
import sys


def sql_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def build_sql(payload):
    environment, release, version = payload['environment'], payload['release'], payload['version']
    mapping = payload['mapping']
    if (not re.fullmatch(r'prod-v[1-9][0-9]*', environment)
            or not release.startswith(environment + '-')
            or mapping['environment'] != environment or mapping['stripe_mode'] != 'test'
            or not re.fullmatch(r'acct_[A-Za-z0-9]+', mapping['stripe_account'])
            or not isinstance(version, int) or version <= 0):
        raise ValueError('invalid generation mapping identity')
    prices = mapping['prices']
    if not prices or len({p['plan_id'] for p in prices}) != len(prices) or len({p['price_id'] for p in prices}) != len(prices):
        raise ValueError('empty or duplicate catalog mapping')
    for p in prices:
        if (not re.fullmatch(r'[A-Z0-9-]+', p['plan_id'])
                or not re.fullmatch(r'price_[A-Za-z0-9]+', p['price_id'])
                or p['currency'] not in ('CNY', 'USD') or p['unit'] not in ('month', 'year', 'once')
                or not isinstance(p['amount'], int) or p['amount'] <= 0):
            raise ValueError('invalid price reference')
    rows = ','.join('(' + ','.join(sql_literal(p[k]) for k in ('plan_id', 'price_id', 'amount', 'currency', 'unit')) + ')' for p in prices)
    receipt_hash = hashlib.sha256(json.dumps(mapping, sort_keys=True).encode()).hexdigest()
    details = json.dumps({'environment': environment, 'release': release, 'operator': 'authorized-maintenance', 'stripe_account': mapping['stripe_account'], 'stripe_mode': 'test', 'mapping_sha256': receipt_hash})
    return f"""BEGIN;
SET LOCAL lock_timeout='10s'; SET LOCAL statement_timeout='60s';
LOCK TABLE public.billing_plans IN SHARE ROW EXCLUSIVE MODE;
CREATE TEMP TABLE reviewed_prices(plan_id text PRIMARY KEY,price_id text UNIQUE,amount bigint,currency text,unit text) ON COMMIT DROP;
INSERT INTO reviewed_prices VALUES {rows};
DO $$ BEGIN
 IF (SELECT count(*) FROM public.schema_migrations WHERE version={version} AND NOT dirty)<>1
    OR (SELECT count(*) FROM public.schema_migrations)<>1 THEN RAISE EXCEPTION 'clean checkpoint mismatch'; END IF;
 IF EXISTS(SELECT 1 FROM reviewed_prices r LEFT JOIN public.billing_plans b USING(plan_id)
   WHERE b.plan_id IS NULL OR NOT b.active OR b.price_amount<>r.amount OR b.price_currency<>r.currency OR b.price_unit<>r.unit
   OR (b.stripe_price_id IS NOT NULL AND b.stripe_price_id<>r.price_id)) THEN RAISE EXCEPTION 'catalog or existing provider mapping mismatch'; END IF;
END $$;
WITH changed AS (
 UPDATE public.billing_plans b SET stripe_price_id=r.price_id, updated_at=now() FROM reviewed_prices r
 WHERE b.plan_id=r.plan_id AND b.stripe_price_id IS DISTINCT FROM r.price_id
 RETURNING b.plan_id,b.stripe_price_id
), audit AS (
 INSERT INTO public.audit_logs(action,actor_uuid,details)
 SELECT 'billing.plan.price_mapping',NULL,{sql_literal(details)}::jsonb || jsonb_build_object('prices',jsonb_agg(to_jsonb(changed)))
 FROM changed HAVING count(*)>0 RETURNING 1
)
SELECT json_build_object('status','passed','environment',{sql_literal(environment)},'release',{sql_literal(release)},'schema_version',{version},'mapped',(SELECT count(*) FROM reviewed_prices),'changed',(SELECT count(*) FROM changed),'audit_records',(SELECT count(*) FROM audit),'mapping_sha256',{sql_literal(receipt_hash)});
COMMIT;
"""


def main():
    payload = json.load(sys.stdin)
    result = subprocess.run(['docker', 'exec', '-i', 'web-saas-postgresql', 'psql', '-U', 'postgres', '-d', 'account', '-XAtq', '-v', 'ON_ERROR_STOP=1'], input=build_sql(payload), text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError('catalog transaction rejected; serving data was not modified')
    print(json.dumps(json.loads(result.stdout.strip())))


if __name__ == '__main__':
    try:
        main()
    except (KeyError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)
