"""Credential-free target execution fixtures; never live production acceptance."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
OWNER = ROOT / 'scripts/data_operations/selfhost'
sys.path.insert(0, str(OWNER))
import native_billing_upgrade_host as HOST
sys.path.pop(0)


def spec(content):
    tables = ['fixture_table_' + str(i).zfill(2) for i in range(52)]
    return {'initialization': dict(schema=1, environment='prod', host='web-saas-prod', database='account',
        accounts_commit='a'*40, image='ghcr.io/ai-workspace-services/accounts:sha-'+'a'*40,
        image_digest='sha256:'+'b'*64, schema_sha256='c'*64,
        business_table_count=52, business_tables=tables, migration_version=2026100601),
        'billing': dict(commit='d'*40, owner='ai-workspace-services/billing-service',
            migration_file='sql/migrations/2026100701_cloud_vendor_costs.up.sql',
            migration_sha256=hashlib.sha256(content).hexdigest(), business_tables=['cloud_vendor_costs'],
            expected_schema_version=2026100601, target_schema_version=2026100701,
            no_business_seeds=True, database_cutover_approved=False)}


class BillingUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.migration = Path(self.temp.name) / '2026100701_cloud_vendor_costs.up.sql'
        content = b'-- fictional test SQL only\nCREATE TABLE cloud_vendor_costs(id uuid);\n'
        self.migration.write_bytes(content)
        self.spec = spec(content)

    def run_owner(self, dry_run, version=2026100601, rows='0', fail_apply=False):
        state = {'version': version}
        calls = []
        manifest = {'format':1, **{k:self.spec['initialization'][k] for k in
            ('schema_sha256','migration_version','business_table_count','business_tables')}}
        env_paths = []
        def sql(query, database='postgres'):
            if 'json_agg' in query:
                tables = self.spec['initialization']['business_tables'] + (['cloud_vendor_costs'] if state['version']==2026100701 else [])
                return json.dumps(sorted(tables))
            if "':'" in query:
                return str(state['version']) + ':false'
            if query.startswith('SELECT version'):
                return str(state['version'])
            if 'count(*)' in query:
                return rows
            raise AssertionError(query)
        def command(argv, **kwargs):
            calls.append(argv)
            if argv[-1]=='native-schema':
                return json.dumps(manifest)
            if '--dsn-env=NATIVE_TARGET_DSN' in argv:
                env_file=Path(argv[argv.index('--env-file')+1]);env_paths.append(env_file)
                self.assertEqual(env_file.stat().st_mode & 0o777,0o600)
                self.assertIn('fictional%24%40%3A',env_file.read_text())
                self.assertIn('--migration-sha256='+self.spec['billing']['migration_sha256'],argv)
                self.assertIn('--expected-version=2026100601',argv)
                self.assertIn('--target-version=2026100701',argv)
                self.assertIn('--entrypoint',argv)
                self.assertNotIn('--dsn=',str(argv))
                if fail_apply: raise HOST.native.Refused('fictional failure')
                state['version']=2026100701
            return ''
        with patch.object(HOST.native,'sql',side_effect=sql), patch.object(HOST.native,'command',side_effect=command), \
             patch.object(HOST,'verify_storage'), patch.object(HOST.native,'remove_execution_container') as cleanup, \
             patch.dict(os.environ,NATIVE_POSTGRES_PASSWORD='fictional$@:',NATIVE_DATA_GATE_VERIFIED='true'):
            if fail_apply:
                with self.assertRaises(HOST.native.Refused):
                    HOST.execute(self.spec,self.migration,OWNER,dry_run)
                cleanup.assert_called_once()
                for path in env_paths:self.assertFalse(path.exists())
                return calls,None
            receipt=HOST.execute(self.spec,self.migration,OWNER,dry_run)
            if env_paths:cleanup.assert_called_once()
            else:cleanup.assert_not_called()
        for path in env_paths:self.assertFalse(path.exists())
        return calls,receipt

    def test_preview_never_runs_migration_or_dsn(self):
        calls,receipt=self.run_owner(True)
        self.assertNotIn('--dsn-env',str(calls))
        self.assertEqual(receipt['business_rows'],0)
        self.assertFalse(receipt['schema_changed'])
        self.assertFalse(receipt['database_cutover_approved'])

    def test_apply_bounded_image_override_and_private_cleanup(self):
        calls,receipt=self.run_owner(False)
        self.assertEqual(receipt['migration_version'],2026100701)
        self.assertEqual(len(receipt['business_tables']),53)
        self.assertTrue(receipt['schema_changed'])
        self.assertEqual(sum('--dsn-env=NATIVE_TARGET_DSN' in c for c in calls),1)
        self.assertNotIn('reset',str(calls))
        self.assertNotIn('createdb',str(calls))

    def test_already_applied_does_not_replay(self):
        calls,receipt=self.run_owner(False,version=2026100701)
        self.assertNotIn('--dsn-env',str(calls))
        self.assertFalse(receipt['schema_changed'])

    def test_timeout_or_failure_cleans_owned_execution_and_credentials(self):
        self.run_owner(False,fail_apply=True)

    def test_nonempty_target_refused(self):
        with self.assertRaises(HOST.native.Refused):self.run_owner(False,rows='1')

    def test_unreviewed_version_refused(self):
        with self.assertRaises(HOST.native.Refused):self.run_owner(False,version=99)

    def test_wrong_hash_scope_source_and_versions_refused(self):
        for key,value in [('commit','main'),('owner','other'),('migration_sha256','0'*64),
                          ('target_schema_version',2026100702),('business_tables',['users']),('no_business_seeds',False)]:
            with self.subTest(key=key):
                candidate=copy.deepcopy(self.spec);candidate['billing'][key]=value
                with self.assertRaises(HOST.native.Refused):HOST.validate_spec(candidate,self.migration)

    def test_symlink_sql_refused(self):
        other=Path(self.temp.name)/'other.sql';other.write_bytes(self.migration.read_bytes())
        self.migration.unlink();self.migration.symlink_to(other)
        with self.assertRaises(HOST.native.Refused):HOST.validate_spec(self.spec,self.migration)

    def test_contract_has_single_owner_and_cleanup(self):
        role=(ROOT/'roles/web_saas_native_billing_upgrade/tasks/main.yml').read_text()
        self.assertIn('  always:',role)
        self.assertIn('no_log: true',role)
        runner=(OWNER/'native_billing_upgrade_runner.sh').read_text()
        self.assertIn('git -C "$BILLING_CHECKOUT" rev-parse HEAD',runner)
        self.assertIn('status --porcelain --untracked-files=all',runner)
        self.assertIn('native_access_guard.sh',runner)
        self.assertNotIn('gcloud',runner)


if __name__=='__main__':unittest.main()
