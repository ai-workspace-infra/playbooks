import importlib.util
import hashlib
import os
from pathlib import Path
import subprocess
import time
import unittest
import urllib.parse
import uuid

path = Path(__file__).resolve().parents[1] / 'roles/accounts_migration_identity/files/business_schema_guard.py'
spec = importlib.util.spec_from_file_location('schema_guard', path)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class GuardTest(unittest.TestCase):
    def test_rejects_untrusted_values(self):
        for rows, system, oid in [({}, '1', '1'), ({'a;DROP TABLE x': (0, 'a'*64)}, '1', '1'),
                                  ({'a': (-1, 'a'*64)}, '1', '1'), ({'a': (0, 'bad')}, '1', '1'),
                                  ({'a': (0, 'a'*64)}, '1;SELECT 1', '1')]:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                guard.build_guard(rows, system, oid)

    def test_pinned_transaction_boundaries(self):
        rows = {'alpha': (0, 'a'*64)}
        source = b'-- fixture\nBEGIN;\nCREATE TABLE public.example(id int);\nCOMMIT;\n'
        digest = hashlib.sha256(source).hexdigest()
        composed = guard.compose_transaction(source, digest, rows, '1', '2', dry_run=True)
        self.assertTrue(composed.rstrip().endswith('ROLLBACK;'))
        self.assertLess(composed.index('LOCK TABLE'), composed.index('CREATE TABLE'))
        with self.assertRaises(ValueError):
            guard.compose_transaction(source, 'b'*64, rows, '1', '2', dry_run=True)
        bad = b'BEGIN;\nCOMMIT;\nCOMMIT;\n'
        with self.assertRaises(ValueError):
            guard.compose_transaction(bad, hashlib.sha256(bad).hexdigest(), rows, '1', '2', dry_run=True)

    def test_guard_with_real_postgres(self):
        dsn = os.environ.get('ACCOUNTS_IDENTITY_TEST_DSN')
        if not dsn:
            self.skipTest('isolated loopback PostgreSQL not configured')
        parsed = urllib.parse.urlparse(dsn)
        if parsed.hostname not in ('localhost', '127.0.0.1') or parsed.username != 'postgres' or parsed.path != '/postgres' or parsed.password:
            self.fail('requires dedicated loopback postgres fixture')
        base = ['psql', '-X', '-q', '-h', parsed.hostname, '-p', str(parsed.port or 5432), '-U', 'postgres', '-At', '-v', 'ON_ERROR_STOP=1']
        name = 'schema_guard_' + uuid.uuid4().hex
        def run(db, query):
            return subprocess.run(base + ['-d', db], input=query, text=True, capture_output=True)
        self.assertEqual(run('postgres', 'CREATE DATABASE "' + name + '";').returncode, 0)
        try:
            self.assertEqual(run(name, "CREATE TABLE public.alpha(id int primary key, value text); INSERT INTO public.alpha VALUES(1,'fixture');").returncode, 0)
            identity = run(name, "SELECT (SELECT system_identifier::text FROM pg_control_system())||'|'||(SELECT oid::text FROM pg_database WHERE datname=current_database());").stdout.strip().split('|')
            sample = run(name, "SET TIME ZONE 'UTC'; SET DateStyle TO 'ISO, YMD'; SET extra_float_digits TO 3; " + guard.fingerprint_query('alpha') + ';')
            self.assertEqual(sample.returncode, 0, sample.stderr)
            count, digest = sample.stdout.strip().split('|')
            sql = guard.build_guard({'alpha': (int(count), digest)}, *identity)
            artifact = b'BEGIN;\nCREATE TABLE public.schema_change(id integer);\nCOMMIT;\n'
            artifact_sha = hashlib.sha256(artifact).hexdigest()
            composed = guard.compose_transaction(artifact, artifact_sha, {'alpha': (int(count), digest)}, *identity, dry_run=True)
            trial = run(name, composed)
            self.assertEqual(trial.returncode, 0, trial.stderr)
            self.assertEqual(run(name, "SELECT to_regclass('public.schema_change') IS NULL;").stdout.strip(), 't')
            bad = guard.compose_transaction(artifact, artifact_sha, {'alpha': (int(count)+1, digest)}, *identity, dry_run=False)
            self.assertNotEqual(run(name, bad).returncode, 0)
            self.assertEqual(run(name, "SELECT to_regclass('public.schema_change') IS NULL;").stdout.strip(), 't')
            ok = run(name, 'BEGIN; ' + sql + " UPDATE public.alpha SET value='changed'; ROLLBACK;")
            self.assertEqual(ok.returncode, 0, ok.stderr)
            self.assertEqual(run(name, 'SELECT value FROM public.alpha;').stdout.strip(), 'fixture')
            holder = subprocess.Popen(base + ['-d', name, '-c', 'BEGIN; ' + sql + ' SELECT pg_sleep(3); ROLLBACK;'],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                      env=dict(os.environ, PGAPPNAME='schema_guard_lock_fixture'))
            try:
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    state = run(name, "SELECT count(*) FROM pg_stat_activity WHERE application_name='schema_guard_lock_fixture' AND wait_event='PgSleep';")
                    if state.stdout.strip() == '1':
                        break
                    time.sleep(.05)
                else:
                    self.fail('guard did not reach the locked observation point')
                competing = run(name, "SET lock_timeout='200ms'; UPDATE public.alpha SET value='other';")
                self.assertNotEqual(competing.returncode, 0)
                self.assertIn('lock timeout', competing.stderr)
            finally:
                _, stderr = holder.communicate(timeout=8)
                self.assertEqual(holder.returncode, 0, stderr)
            drift = guard.build_guard({'alpha': (int(count) + 1, digest)}, *identity)
            denied = run(name, 'BEGIN; ' + drift + " UPDATE public.alpha SET value='changed'; COMMIT;")
            self.assertNotEqual(denied.returncode, 0)
            self.assertIn('target data differs from restore', denied.stderr)
            self.assertEqual(run(name, 'SELECT value FROM public.alpha;').stdout.strip(), 'fixture')
            wrong = guard.build_guard({'alpha': (int(count), digest)}, '1', identity[1])
            self.assertNotEqual(run(name, 'BEGIN; ' + wrong + ' COMMIT;').returncode, 0)
            self.assertEqual(run(name, 'CREATE TABLE public.extra(id integer);').returncode, 0)
            scope = run(name, 'BEGIN; ' + sql + ' COMMIT;')
            self.assertNotEqual(scope.returncode, 0)
            self.assertIn('target public table scope differs', scope.stderr)
        finally:
            self.assertEqual(run('postgres', 'DROP DATABASE "' + name + '" WITH (FORCE);').returncode, 0)


if __name__ == '__main__':
    unittest.main()
