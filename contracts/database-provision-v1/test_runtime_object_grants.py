import unittest

from runtime_object_grants import plan_runtime_grants


class RuntimeObjectGrantsTests(unittest.TestCase):
    def setUp(self):
        self.spec = {
            'database': 'account', 'schema': 'public',
            'runtimes': [
                {'role': 'accounts_rw', 'tables': {'mfa_recovery_codes': ['SELECT', 'INSERT', 'UPDATE', 'DELETE']}},
                {'role': 'billing_rw', 'tables': {'billing_ledger': ['SELECT', 'INSERT', 'UPDATE']}},
            ],
            'rls_policies': [{'name': 'accounts_runtime_access', 'role': 'accounts_rw', 'table': 'mfa_recovery_codes'}],
        }

    def test_only_declared_objects_and_role_receive_policy(self):
        plan = plan_runtime_grants(self.spec)
        sql = '\n'.join(plan.statements)
        self.assertIn('ON "public"."mfa_recovery_codes" FOR ALL TO "accounts_rw"', sql)
        self.assertNotIn('TO PUBLIC', sql)
        self.assertNotIn('ALL TABLES', sql)
        self.assertNotIn('ON TABLE "public"."mfa_recovery_codes" TO "billing_rw"', sql)
        self.assertEqual(plan, plan_runtime_grants(self.spec))

    def test_rejects_unauthorized_or_duplicate_policies(self):
        self.spec['rls_policies'][0]['role'] = 'billing_rw'
        with self.assertRaises(ValueError):
            plan_runtime_grants(self.spec)
        self.spec['rls_policies'][0]['role'] = 'accounts_rw'
        self.spec['rls_policies'].append(dict(self.spec['rls_policies'][0]))
        with self.assertRaises(ValueError):
            plan_runtime_grants(self.spec)

    def test_rejects_sql_injection_and_wildcards(self):
        self.spec['runtimes'][0]['role'] = 'accounts; DROP ROLE postgres'
        with self.assertRaises(ValueError):
            plan_runtime_grants(self.spec)
        self.spec['runtimes'][0]['role'] = 'public'
        with self.assertRaises(ValueError):
            plan_runtime_grants(self.spec)
        self.spec['runtimes'][0]['role'] = 'accounts_rw'
        self.spec['runtimes'][0]['tables']['mfa_recovery_codes'] = ['ALL']
        with self.assertRaises(ValueError):
            plan_runtime_grants(self.spec)


if __name__ == '__main__':
    unittest.main()
