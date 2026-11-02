import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import urllib.parse
import uuid

directory = Path(__file__).resolve().parents[1] / 'roles/accounts_migration_identity/files'
sys.path.insert(0, str(directory))
spec = importlib.util.spec_from_file_location('schema_preflight', directory / 'business_schema_preflight.py')
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


class PreflightTest(unittest.TestCase):
    def test_socket_requires_private_owner_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(preflight.private_socket(directory), directory)
            os.chmod(directory, 0o755)
            with self.assertRaises(RuntimeError):
                preflight.private_socket(directory)

    def test_fingerprints_on_isolated_postgres(self):
        dsn = os.environ.get('ACCOUNTS_IDENTITY_TEST_DSN')
        if not dsn:
            self.skipTest('isolated loopback PostgreSQL not configured')
        parsed = urllib.parse.urlparse(dsn)
        if parsed.hostname not in ('localhost', '127.0.0.1') or parsed.username != 'postgres' or parsed.path != '/postgres' or parsed.password:
            self.fail('requires dedicated loopback postgres fixture')
        base = ['psql', '-X', '-q', '-h', parsed.hostname, '-p', str(parsed.port or 5432), '-U', 'postgres', '-At', '-v', 'ON_ERROR_STOP=1']
        name = 'schema_preflight_' + uuid.uuid4().hex
        def run(db, query):
            return subprocess.run(base + ['-d', db], input=query, text=True, capture_output=True)
        self.assertEqual(run('postgres', 'CREATE DATABASE "' + name + '";').returncode, 0)
        try:
            self.assertEqual(run(name, "CREATE TABLE public.alpha(id int); CREATE TABLE public.beta(id int); INSERT INTO public.alpha VALUES (1),(2);").returncode, 0)
            got = preflight.fingerprints(base + ['-d', name])
            self.assertEqual(set(got), {'alpha', 'beta'})
            self.assertEqual(got['alpha'][0], 2)
            self.assertEqual(got['beta'][0], 0)
            self.assertNotEqual(got['alpha'][1], got['beta'][1])
            self.assertEqual(got, preflight.fingerprints(base + ['-d', name]))
        finally:
            self.assertEqual(run('postgres', 'DROP DATABASE "' + name + '" WITH (FORCE);').returncode, 0)


if __name__ == '__main__':
    unittest.main()
