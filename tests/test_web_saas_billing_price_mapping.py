import copy
import importlib.util
from pathlib import Path
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


if __name__ == '__main__': unittest.main()
