import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import unittest

path = Path(__file__).resolve().parents[1] / 'roles/web_saas_billing_price_mapping/files/apply.py'
spec = importlib.util.spec_from_file_location('price_mapping', path)
mapping = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mapping)


class MappingTest(unittest.TestCase):
    def setUp(self):
        self.payload = dict(environment='prod-v3', release='prod-v3-catalog-r1', version=2026101902,
                            mapping=dict(environment='prod-v3', stripe_mode='test', stripe_account='acct_test', prices=[
                                dict(plan_id='PLUS', price_id='price_test', amount=2000, currency='CNY', unit='month')]))

    def test_bounded_transaction_preserves_history(self):
        sql = mapping.build_sql(self.payload)
        self.assertIn('BEGIN;', sql)
        self.assertIn('billing.plan.price_mapping', sql)
        self.assertIn('b.price_amount<>r.amount', sql)
        self.assertIn('b.stripe_price_id IS NOT NULL', sql)
        self.assertNotIn('UPDATE public.users', sql)
        self.assertNotIn('UPDATE public.subscription', sql)

    def test_rejects_live_cross_generation_and_duplicate_mapping(self):
        for mutation in ('live', 'cross-generation', 'duplicate', 'invalid-price'):
            value = copy.deepcopy(self.payload)
            if mutation == 'live': value['mapping']['stripe_mode'] = 'live'
            elif mutation == 'cross-generation': value['mapping']['environment'] = 'prod-v2'
            elif mutation == 'duplicate': value['mapping']['prices'] *= 2
            else: value['mapping']['prices'][0]['price_id'] = "price_test'; DROP TABLE users;--"
            with self.assertRaises(ValueError): mapping.build_sql(value)

    @unittest.skipUnless(os.environ.get('MAPPING_TEST_LOCAL_CONTAINER') == 'accounts-catalog-runtime-test-20261019', 'dedicated local PostgreSQL fixture only')
    def test_transaction_replay_and_conflict_rollback_postgres(self):
        container = os.environ['MAPPING_TEST_LOCAL_CONTAINER']
        subprocess.run(['docker', 'exec', container, 'createdb', '-U', 'postgres', 'catalog_mapping_test'], check=True, capture_output=True)
        def query(body):
            return subprocess.run(['docker', 'exec', '-i', container, 'psql', '-U', 'postgres', '-d', 'catalog_mapping_test', '-XAtq', '-v', 'ON_ERROR_STOP=1'], input=body, text=True, capture_output=True)
        try:
            setup = query("CREATE TABLE schema_migrations(version bigint,dirty bool); INSERT INTO schema_migrations VALUES(2026101902,false); CREATE TABLE billing_plans(plan_id text PRIMARY KEY,stripe_price_id text UNIQUE,active bool,price_amount bigint,price_currency text,price_unit text,updated_at timestamptz); INSERT INTO billing_plans VALUES('PLUS',NULL,true,2000,'CNY','month',now()); CREATE TABLE audit_logs(action text,actor_uuid uuid,details jsonb);")
            self.assertEqual(setup.returncode, 0, setup.stderr)
            first = query(mapping.build_sql(self.payload))
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(json.loads(first.stdout)['changed'], 1)
            self.assertEqual(json.loads(query(mapping.build_sql(self.payload)).stdout)['changed'], 0)
            bad = copy.deepcopy(self.payload); bad['mapping']['prices'][0]['amount'] = 2001
            self.assertNotEqual(query(mapping.build_sql(bad)).returncode, 0)
            self.assertEqual(query("SELECT stripe_price_id FROM billing_plans; SELECT count(*) FROM audit_logs;").stdout.strip(), 'price_test\n1')
        finally:
            subprocess.run(['docker', 'exec', container, 'dropdb', '-U', 'postgres', 'catalog_mapping_test'], check=True, capture_output=True)


if __name__ == '__main__': unittest.main()
