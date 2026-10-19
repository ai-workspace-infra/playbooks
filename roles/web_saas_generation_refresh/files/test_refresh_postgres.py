"""Opt-in disposable PostgreSQL 17 restore test, no deployed hosts or credentials."""
import json
import os
from pathlib import Path
import subprocess
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

from test_refresh import refresh, ContractTests


@unittest.skipUnless(os.environ.get('RUN_POSTGRES_TESTS') == '1', 'explicit opt-in required')
class RestoreTests(unittest.TestCase):
    def test_snapshot_and_candidate(self):
        container = subprocess.check_output(['docker', 'run', '-d', '--rm',
            '--network', 'none', '--tmpfs', '/var/lib/postgresql/data',
            '-e', 'POSTGRES_HOST_AUTH_METHOD=trust', 'postgres:17'], text=True).strip()
        self.assertRegex(container, r'^[0-9a-f]{64}$')
        original_db = refresh.DB
        class FixtureDB(original_db):
            def __init__(self, database='account'):
                super().__init__(database, container)
        try:
            for _ in range(60):
                ready = subprocess.run(['docker', 'exec', container, 'pg_isready', '-h', '127.0.0.1', '-U', 'postgres'], capture_output=True)
                if ready.returncode == 0:
                    break
                time.sleep(0.2)
            else:
                self.fail('fixture did not start')
            admin = FixtureDB('postgres')
            admin.sql('CREATE DATABASE account')
            admin.sql('CREATE ROLE recovery_app NOSUPERUSER NOBYPASSRLS')
            db = FixtureDB()
            db.sql('''CREATE TABLE schema_migrations(version bigint, dirty boolean);
                INSERT INTO schema_migrations VALUES(2026101703,false);
                CREATE TABLE users(id bigint PRIMARY KEY, password text, proxy_uuid text);
                INSERT INTO users VALUES(1,'fixture-password-hash','fixture-uuid');
                CREATE TABLE subscription_records(id bigint PRIMARY KEY, state text);
                INSERT INTO subscription_records VALUES(1,'active');
                CREATE TABLE billing_ledger(id bigint PRIMARY KEY, amount numeric);
                INSERT INTO billing_ledger VALUES(1,12.50);
                CREATE SCHEMA finance;
                CREATE TABLE finance.invoices(id bigint PRIMARY KEY, state text);
                INSERT INTO finance.invoices VALUES(1,'paid');''')
            db.sql('''CREATE TABLE billing_plans (
                plan_id text PRIMARY KEY, stripe_price_id text UNIQUE,
                display_name text NOT NULL DEFAULT '', kind text NOT NULL DEFAULT 'subscription',
                included_quota_bytes bigint NOT NULL DEFAULT 0, package_name text NOT NULL DEFAULT 'default',
                price_amount bigint NOT NULL DEFAULT 0, price_currency text NOT NULL DEFAULT '',
                price_unit text NOT NULL DEFAULT '', price_multipliers jsonb NOT NULL DEFAULT '{}',
                features jsonb NOT NULL DEFAULT '{}', trial_days integer NOT NULL DEFAULT 0,
                active boolean NOT NULL DEFAULT true, sort_order integer NOT NULL DEFAULT 0,
                created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now());''')
            admin.sql('ALTER DATABASE account SET default_transaction_read_only=on')
            case = ContractTests()
            case.setUp()
            c = case.contract
            with tempfile.TemporaryDirectory(prefix='refresh-test-') as folder:
                root = Path(folder).resolve()
                with patch.object(refresh, 'DB', FixtureDB), patch.object(refresh, 'Source', lambda c, image: FixtureDB()), patch.object(refresh, 'LIVE', container):
                    source = refresh.source_snapshot(c, root, 'fixture')
                    restored = refresh.restore_candidate(c, root)
                    self.assertEqual(source['fingerprints'], restored['fingerprints'])
                    self.assertEqual(restored['fingerprints']['public.users']['rows'], 1)
                    self.assertTrue(restored['source_fingerprints_equal'])
                    with self.assertRaises(Exception):
                        refresh.restore_candidate(c, root)
                    self.assertEqual(admin.sql("SELECT count(*) FROM pg_database WHERE datname='account'"), '1')
                    candidate = FixtureDB(restored['candidate_database'])
                    if os.environ.get('ACCOUNTS_TEST_MIGRATECTL'):
                        binary = root / 'migratectl'
                        shutil.copyfile(os.environ['ACCOUNTS_TEST_MIGRATECTL'], binary)
                        binary.chmod(0o500)
                        c['migration_binary'] = str(binary)
                        c['migration_binary_sha256'] = refresh.sha(binary)
                        image = json.loads(subprocess.check_output(['docker', 'inspect', container]))[0]['Image']
                        # Fixture uses trust auth; no secret is involved.
                        with patch.dict(os.environ, {}):
                            real_run = refresh.run
                            def fixture_run(args, **kwargs):
                                if args[:2] == ['docker', 'inspect']:
                                    info = json.loads(real_run(args, **kwargs))
                                    info[0]['Config']['Env'].append('POSTGRES_PASSWORD=fixture')
                                    return json.dumps(info).encode()
                                return real_run(args, **kwargs)
                            with patch.object(refresh, 'run', fixture_run):
                                upgraded = refresh.upgrade_candidate(c, root, image)
                        self.assertEqual(len(upgraded['migrations']), 2)
                        self.assertTrue(all(s['isolated_restore_verified'] for s in upgraded['migrations']))
                    else:
                        # Rename-only test; not a migration acceptance claim.
                        candidate.sql('UPDATE schema_migrations SET version=2026101902')
                        refresh.write_receipt(root / 'candidate-upgraded.json', dict(
                            candidate_database=restored['candidate_database'], candidate_oid=restored['candidate_oid'],
                            fingerprints=refresh.fingerprints(candidate), migrations=[]))
                    old_oid = admin.sql("SELECT oid FROM pg_database WHERE datname='account'")
                    with patch.object(refresh, 'sandbox_catalog', return_value={'fixture': True}):
                        activated = refresh.activate(c, root)
                    self.assertEqual(activated['previous_oid'], old_oid)
                    self.assertEqual(activated['active_oid'], restored['candidate_oid'])
                    self.assertEqual(FixtureDB().sql('SELECT count(*) FROM users'), '1')
                    with self.assertRaises(Exception):
                        refresh.activate(c, root)
        except subprocess.CalledProcessError as exc:
            self.fail('Disposable fixture SQL failed: ' + (exc.stderr or b'').decode())
        finally:
            subprocess.run(['docker', 'rm', '-f', container], check=True, capture_output=True)


if __name__ == '__main__':
    unittest.main()
