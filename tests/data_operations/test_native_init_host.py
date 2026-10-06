"""Target-only native initialization fixtures; never production acceptance."""
import importlib.util
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
LOADER = importlib.util.spec_from_file_location('native_init_host', ROOT / 'scripts/data_operations/selfhost/native_init_host.py')
HOST = importlib.util.module_from_spec(LOADER)
LOADER.loader.exec_module(HOST)


def spec():
    return dict(schema=1, environment='prod', host='web-saas-prod', database='account',
        accounts_commit='a' * 40, image='ghcr.io/ai-workspace-services/accounts:sha-' + 'a' * 40,
        image_digest='sha256:' + 'b' * 64, schema_sha256='c' * 64,
        business_table_count=2, business_tables=['billing_ledger', 'users'], migration_version=42)


class NativeInitHostTests(unittest.TestCase):
    def commands(self, config, present='0', rejected_guard=False, wrong_manifest=False, wrong_receipt=False):
        calls = []
        manifest = {k: config[k] for k in ('schema_sha256', 'migration_version', 'business_table_count', 'business_tables')}
        manifest['format'] = 1
        if wrong_manifest:
            manifest['schema_sha256'] = '0' * 64

        def command(argv, **kwargs):
            calls.append(argv)
            if argv[0] == 'bash':
                if rejected_guard and 'init_guard_host.sh' in argv[1]:
                    raise HOST.Refused('fictional nonempty target')
                return ''
            if argv[-1] == 'SHOW server_version_num':
                return '170005'
            if 'SELECT count(*) FROM pg_database' in argv[-1]:
                return present
            if argv[-1] == 'native-schema':
                return json.dumps(manifest)
            if '--dsn-env=NATIVE_TARGET_DSN' in argv:
                file = Path(argv[argv.index('--env-file') + 1])
                self.assertEqual(file.stat().st_mode & 0o777, 0o600)
                self.assertIn('secret%24%40%3A', file.read_text())
                result = {'result': 'eligible' if '--dry-run=true' in argv else 'initialized',
                    'environment': 'prod', 'database': 'account', 'business_rows': 0,
                    'database_cutover_approved': False,
                    **{k: config[k] for k in ('schema_sha256', 'migration_version', 'business_tables')}}
                if wrong_receipt:
                    result['business_rows'] = 1
                return json.dumps(result)
            return ''
        return calls, command

    def execute(self, config, dry_run, **flags):
        calls, handler = self.commands(config, **flags)
        with patch.object(HOST, 'command', side_effect=handler), patch.object(HOST, 'remove_execution_container') as cleanup, patch.dict(os.environ, NATIVE_POSTGRES_PASSWORD='secret$@:'):
            receipt = HOST.execute(config, dry_run, ROOT / 'scripts/data_operations/selfhost')
        if any('--dsn-env=NATIVE_TARGET_DSN' in c for c in calls):
            cleanup.assert_called_once()
        return calls, receipt

    def test_absent_preview_does_not_create_database_or_connect_with_dsn(self):
        calls, receipt = self.execute(spec(), True)
        self.assertEqual(receipt['result'], 'eligible_absent_database')
        self.assertFalse(receipt['database_created'])
        self.assertFalse(receipt['schema_initialized'])
        self.assertNotIn('createdb', str(calls))
        self.assertNotIn('--dsn-env', str(calls))
        self.assertFalse(receipt['database_cutover_approved'])

    def test_existing_empty_preview_runs_compiled_readonly_check(self):
        calls, receipt = self.execute(spec(), True, present='1')
        self.assertEqual(receipt['result'], 'eligible')
        self.assertNotIn('createdb', str(calls))
        self.assertIn('--dry-run=true', str(calls))

    def test_apply_uses_digest_override_and_cleans_private_dsn(self):
        config = spec()
        calls, receipt = self.execute(config, False)
        self.assertEqual(receipt['stage'], 'native_schema_initialized')
        self.assertTrue(receipt['database_created'])
        self.assertEqual(sum('createdb' in c for c in calls), 1)
        init = next(c for c in calls if '--dsn-env=NATIVE_TARGET_DSN' in c)
        self.assertIn('--entrypoint', init)
        self.assertIn('/usr/local/bin/migratectl', init)
        self.assertIn(HOST.validate_spec(config), init)
        self.assertIn('container:web-saas-postgresql', init)
        self.assertNotIn('secret', str(calls))
        self.assertFalse(Path(init[init.index('--env-file') + 1]).exists())
        self.assertNotIn('dropdb', str(calls))

    def test_nonempty_target_refuses_before_pull_and_creation(self):
        config = spec()
        calls, handler = self.commands(config, rejected_guard=True)
        with patch.object(HOST, 'command', side_effect=handler):
            with self.assertRaises(HOST.Refused):
                HOST.execute(config, False, ROOT / 'scripts/data_operations/selfhost')
        self.assertNotIn('pull', str(calls))
        self.assertNotIn('createdb', str(calls))

    def test_wrong_compiled_manifest_refuses_before_any_database_creation(self):
        config = spec()
        calls, handler = self.commands(config, wrong_manifest=True)
        with patch.object(HOST, 'command', side_effect=handler):
            with self.assertRaises(HOST.Refused):
                HOST.execute(config, False, ROOT / 'scripts/data_operations/selfhost')
        self.assertNotIn('createdb', str(calls))

    def test_invalid_scope_or_image_refuses_before_command(self):
        for key, value in [('environment', 'uat'), ('database', 'postgres'), ('image', 'accounts:latest'),
                           ('image_digest', 'latest'), ('schema_sha256', 'x'), ('business_tables', ['users', 'users'])]:
            config = spec()
            config[key] = value
            with patch.object(HOST, 'command') as command:
                with self.assertRaises(HOST.Refused):
                    HOST.execute(config, False, ROOT / 'scripts/data_operations/selfhost')
                command.assert_not_called()

    def test_receipt_cannot_claim_business_rows_or_cutover(self):
        config = spec()
        with self.assertRaises(HOST.Refused):
            self.execute(config, False, present='1', wrong_receipt=True)
        receipt = dict(result='initialized', environment='prod', database='account', business_rows=0,
            database_cutover_approved=True,
            **{k: config[k] for k in ('schema_sha256', 'migration_version', 'business_tables')})
        with self.assertRaises(HOST.Refused):
            HOST.validate_receipt(receipt, config, False)

    def test_command_failure_does_not_echo_raw_output(self):
        from types import SimpleNamespace
        with patch.object(HOST.subprocess, 'run', return_value=SimpleNamespace(returncode=1, stdout='secret', stderr='password')):
            with self.assertRaises(HOST.Refused) as failure:
                HOST.command(['fictional-command'])
        self.assertNotIn('secret', str(failure.exception))
        self.assertNotIn('password', str(failure.exception))

    def test_only_exact_missing_execution_container_is_clean(self):
        from types import SimpleNamespace
        name = 'native-schema-init-fictional'
        for status, message, accepted in [(0, '', True), (1, 'Error: No such container: ' + name, True),
            (1, 'Error response from daemon: No such container: ' + name, True),
            (1, 'permission denied', False), (1, 'Error: No such container: another', False)]:
            with patch.object(HOST.subprocess, 'run', return_value=SimpleNamespace(returncode=status, stderr=message)):
                if accepted:
                    HOST.remove_execution_container(name)
                else:
                    with self.assertRaises(HOST.Refused):
                        HOST.remove_execution_container(name)

    def test_no_application_service_or_source_is_part_of_play(self):
        import yaml
        play = yaml.safe_load((ROOT / 'initialize-web-saas-native.yml').read_text())[0]
        self.assertEqual(play['hosts'], 'web-saas-prod')
        self.assertEqual(play['roles'], ['roles/web_saas_native_init'])
        action = yaml.safe_load((ROOT / '.github/actions/prod-native-init/action.yml').read_text())
        self.assertEqual(action['inputs']['dry_run']['default'], 'true')
        self.assertEqual(action['inputs']['data_gate_verified']['default'], 'false')
        self.assertNotIn('source_dsn', action['inputs'])


if __name__ == '__main__':
    unittest.main()
