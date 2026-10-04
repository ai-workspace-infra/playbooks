import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('role_runner', ROOT / 'scripts/data_operations/selfhost/role_runner.py')
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


class RoleRunnerTests(unittest.TestCase):
    def env(self, **changes):
        env = dict(REQUESTED_ENVIRONMENT='uat', REQUESTED_OPERATION='preflight', RELEASE_TAG='daily-build-2026.10.04',
                   EXPECTED_SCHEMA_VERSION='2026092801', CONFIG_JSON=json.dumps(dict(execution_path='selfhost_roles',
                   target_host='web-saas-uat', caller_run_id='123')))
        env.update(changes)
        return env

    def test_preflight_leaves_missing_provenance_to_role_guard(self):
        RUNNER.validate(self.env())

    def test_prod_mutations_and_credentials_rejected(self):
        for env in (self.env(REQUESTED_ENVIRONMENT='prod'), self.env(REQUESTED_OPERATION='migrate'),
                    self.env(RELEASE_TAG='main'), self.env(CONFIG_JSON='{"password":"hidden"}')):
            with self.subTest(env=env), self.assertRaises(ValueError):
                RUNNER.validate(env)

    def test_backup_requires_explicit_provenance_and_opt_in(self):
        config = json.loads(self.env()['CONFIG_JSON'])
        with self.assertRaises(ValueError):
            RUNNER.validate(self.env(REQUESTED_OPERATION='backup'))
        config.update(confirm_backup=True, source_database_id='uat-selfhost-account', baseline_id='uat-baseline-1',
                      authorized_subscription_sample_id='authorized-test-sample')
        RUNNER.validate(self.env(REQUESTED_OPERATION='backup', CONFIG_JSON=json.dumps(config)))

    def test_inventory_has_only_cmdb_target_and_drops_executable_host_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            cmdb = Path(directory) / 'cmdb.json'
            cmdb.write_text(json.dumps(dict(environment='uat', **{'web-saas-uat': dict(ip='192.0.2.1', ansible_user='deployer',
                groups=['web_saas'], host_vars={'ansible_connection':'local'}), 'other':dict(ip='192.0.2.2')})))
            env = self.env(RUNNER_TEMP=directory, CMDB_FILE=str(cmdb), GITHUB_RUN_ID='42')
            with patch.object(RUNNER.subprocess, 'run') as run:
                run.return_value.stdout = 'a' * 40
                RUNNER.prepare(env)
            selected = json.loads((Path(directory) / 'selfhost-data-roles/cmdb.json').read_text())
            self.assertEqual(set(selected), {'web-saas-uat'})
            self.assertNotIn('host_vars', selected['web-saas-uat'])
            variables = json.loads((Path(directory) / 'selfhost-data-roles/vars.json').read_text())
            self.assertNotIn('web_saas_release_backup_passphrase', variables)


if __name__ == '__main__':
    unittest.main()
