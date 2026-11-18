"""Offline safety-contract tests; never connect to a deployed database."""
import copy
import importlib.util
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('generation_pg', HERE.parents[1] / 'web_saas_stripe_sandbox/files/prepare.py')
pg = importlib.util.module_from_spec(spec)
sys.modules['generation_pg'] = pg
spec.loader.exec_module(pg)
spec = importlib.util.spec_from_file_location('refresh', HERE / 'refresh.py')
refresh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(refresh)


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.contract = dict(source_generation='prod-v2', target_generation='prod-v3',
            run_id='20261019190000', release_tag='prod-v3-reviewed-r1',
            expected_source_version=2026101703, target_version=2026101902,
            public_dns_cutover=False, allow_refresh=True, gitops_sha='a' * 40,
            migration_binary_sha256='b' * 64,
            migration_binary='/usr/local/libexec/accounts-reconcile/' + 'b' * 64,
            passphrase='fixture-only-not-a-real-key-' * 2,
            source_dsn='postgresql://postgres:fixture@127.0.0.1:35432/account',
            migrations=[dict(expected=2026101703, target=2026101901,
                sha256='b9fee298512e7d888fe619917c8238120f0e227f4ea348e59397d074c41f878d'),
                dict(expected=2026101901, target=2026101902,
                sha256='53f9aaf7c56a14fd7c132c6649b79582cd2063f8903b94efb4e23cbf4895437c')])

    def test_reviewed_contract(self):
        refresh.validate(self.contract, 'web-saas-prod-v3')

    def test_wrong_host(self):
        with self.assertRaises(Exception):
            refresh.validate(self.contract, 'web-saas-prod-v2')

    def test_reject_unsafe_contracts(self):
        for key, value in [('source_generation', 'prod-v3'), ('allow_refresh', False),
                ('public_dns_cutover', True), ('run_id', '../account'),
                ('target_version', 2026101703), ('gitops_sha', 'main'),
                ('migration_binary', '/tmp/unreviewed'),
                ('source_dsn', 'postgresql://postgres:fixture@public.example/account')]:
            with self.subTest(key=key):
                c = copy.deepcopy(self.contract)
                c[key] = value
                with self.assertRaises(Exception):
                    refresh.validate(c, 'web-saas-prod-v3')

    def test_migration_checksum_is_bound(self):
        self.contract['migrations'][0]['sha256'] = '0' * 64
        with self.assertRaises(Exception):
            refresh.validate(self.contract, 'web-saas-prod-v3')

    def test_database_names_are_bounded(self):
        for name in ['account; DROP DATABASE postgres', 'account_before_bad', '../account']:
            with self.assertRaises(Exception):
                refresh.DB(name)

    def test_credentials_not_in_client_arguments(self):
        source = refresh.Source(self.contract, 'sha256:' + 'c' * 64)
        argv = source.command('pg_dump', '-d', 'account')
        self.assertNotIn('fixture', ' '.join(argv))
        self.assertIn('PGPASSWORD', argv)
        with self.assertRaises(Exception):
            source.command('bash')


if __name__ == '__main__':
    unittest.main()
