import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'roles/web_saas_usage_reconciliation/files/reconcile.sh'


class UsageReconciliationTest(unittest.TestCase):
    def run_fixture(self, apply=False, conflict=False, bad_checksum=False, host_mode=False, public_source=False):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            binary = path / 'migratectl'
            binary.write_text('''#!/usr/bin/env python3
import json, os, sys
with open(os.environ['CALL_LOG'], 'a') as f: f.write(json.dumps(sys.argv[1:])+'\\n')
assert os.environ['MIGRATION_TARGET_DSN'].startswith('postgresql://postgres:')
if os.environ.get('CONFLICT') == 'true': sys.exit(3)
applied='--apply' in sys.argv
print(json.dumps(dict(result='merged' if applied else 'preview', conflicts=0,
  plan_sha256='a'*64, source_read_only=True, target_writes=applied,
  schema_before_sha256='b'*64, schema_after_sha256='b'*64,
  users_before=dict(count=1), users_after=dict(count=1))))
''')
            binary.chmod(0o700)
            digest = hashlib.sha256(binary.read_bytes()).hexdigest()
            log = path / 'calls'
            docker = path / 'docker'
            docker.write_text('''#!/usr/bin/env python3
import json, os, sys
a=sys.argv[1:]
with open(os.environ['CALL_LOG'], 'a') as f: f.write(json.dumps(a)+'\\n')
if 'sha256sum' in a: print(os.environ['MERGE_BINARY_SHA256']+'  fixture')
if a == ['inspect', 'web-saas-postgresql']:
    print(json.dumps([dict(Name='/web-saas-postgresql', State=dict(Running=True),
      Mounts=[dict(Type='bind',Source='/data/postgresql')],
      NetworkSettings=dict(Networks=dict(internal=dict(IPAddress='172.19.0.2'))),
      Config=dict(Env=['POSTGRES_USER=postgres','POSTGRES_PASSWORD=fixture-only-target-secret']))]))
if 'reconcile-usage' in a:
    if os.environ.get('CONFLICT') == 'true': sys.exit(3)
    applied='--apply' in a
    print(json.dumps(dict(result='merged' if applied else 'preview', conflicts=0,
      plan_sha256='a'*64, source_read_only=True, target_writes=applied,
      schema_before_sha256='b'*64, schema_after_sha256='b'*64,
      users_before=dict(count=1), users_after=dict(count=1))))
''')
            docker.chmod(0o700)
            env = dict(os.environ, PATH=tmp+os.pathsep+os.environ['PATH'],
                       MERGE_BINARY=str(binary), MERGE_BINARY_SHA256='0'*64 if bad_checksum else digest,
                       MERGE_SCHEMA_SHA256='b'*64, MERGE_BILLING_SHA256='c'*64,
                       MERGE_RUN='2026101901', MERGE_VERSION='2026101902',
                       MERGE_APPLY=str(apply).lower(), MIGRATION_SOURCE_DSN='not-a-real-secret',
                       MERGE_EXECUTION_MODE='host-loopback' if host_mode else 'container',
                       CALL_LOG=str(log), CONFLICT=str(conflict).lower())
            source = ('postgresql://postgres:fixture-only-source-secret@' +
                      ('example.invalid' if public_source else '127.0.0.1') + ':35432/account') if host_mode else 'not-a-real-secret'
            result = subprocess.run(['bash', str(SCRIPT)], env=env, input=source+'\n', capture_output=True, text=True)
            calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
            return result, calls

    def test_preview_never_applies(self):
        result, calls = self.run_fixture()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any('--apply' in call for call in calls))
        self.assertEqual(json.loads(result.stdout)['result'], 'preview')

    def test_apply_binds_preview_and_keeps_dsn_out_of_argv(self):
        result, calls = self.run_fixture(apply=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        apply = next(call for call in calls if '--apply' in call)
        self.assertIn('--expected-plan-sha256', apply)
        self.assertIn('a'*64, apply)
        self.assertNotIn('not-a-real-secret', json.dumps(calls))

    def test_conflict_prevents_apply_and_cleans_staged_binary(self):
        result, calls = self.run_fixture(apply=True, conflict=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any('--apply' in call for call in calls))
        self.assertTrue(any('rm' in call for call in calls))

    def test_bad_binary_checksum_stops_before_docker(self):
        result, calls = self.run_fixture(apply=True, bad_checksum=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])

    def test_host_loopback_binds_plan_without_secret_argv_or_container_mutation(self):
        result, calls = self.run_fixture(apply=True, host_mode=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['result'], 'merged')
        self.assertTrue(any('--expected-plan-sha256' in call for call in calls))
        serialized = json.dumps(calls)
        self.assertNotIn('fixture-only-source-secret', serialized)
        self.assertNotIn('fixture-only-target-secret', serialized)
        self.assertFalse(any('exec' in call or 'cp' in call for call in calls))

    def test_host_loopback_rejects_public_source_before_target_access(self):
        result, calls = self.run_fixture(host_mode=True, public_source=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])

    def test_host_loopback_conflict_never_applies(self):
        result, calls = self.run_fixture(apply=True, host_mode=True, conflict=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any('--apply' in call for call in calls))

    def test_entry_orders_preflight_backup_restore_then_merge(self):
        text = (ROOT / 'web-saas-generation-usage-reconciliation.yml').read_text()
        self.assertLess(text.index('tasks_from: preflight'), text.index('import_playbook:'))
        self.assertLess(text.index('import_playbook:'), text.index('Preview and optionally merge'))
        gates = (ROOT / 'roles/web_saas_usage_reconciliation/tasks/main.yml').read_text()
        for required in ['isolated_restore_verified', 'restored_data_matches', 'archive_sha256',
                         'database_system_identifier', 'migration_dirty', 'restore_database']:
            self.assertIn(required, gates)

    def test_target_writer_and_binary_staging_boundaries(self):
        tasks = ROOT / 'roles/web_saas_usage_reconciliation/tasks'
        writer = (tasks / 'writer_state.yml').read_text()
        self.assertIn("inventory_hostname == 'web-saas-prod-v3'", writer)
        self.assertIn("in ['pause', 'resume']", writer)
        self.assertIn("[doco-cd, web-saas-accounts, web-saas-billing]", writer)
        self.assertIn("[web-saas-billing, web-saas-accounts, doco-cd]", writer)
        self.assertNotIn('web-saas-postgresql', writer)
        prepare = (tasks / 'prepare_executor.yml').read_text()
        for guard in ['checksum_algorithm: sha256', 'not generation_executor.stat.islnk',
                      "generation_executor.stat.pw_name == 'root'", 'force: false']:
            self.assertIn(guard, prepare)


if __name__ == '__main__':
    unittest.main()
