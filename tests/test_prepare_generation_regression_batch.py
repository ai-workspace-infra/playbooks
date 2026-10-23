"""Offline contract fixtures for the V5-candidate/V4-source batch planner."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PLANNER_PATH = ROOT / 'scripts/prepare_generation_regression_batch.py'
RUNNER_PATH = ROOT / 'roles/web_saas_generation_fresh/files/regression_cases.py'


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


planner = load_module('generation_regression_batch_planner', PLANNER_PATH)
runner = load_module('generation_regression_host_runner', RUNNER_PATH)


class GenerationRegressionBatchTests(unittest.TestCase):
    def args(self, **overrides):
        values = {
            'target_generation': 'prod-v5', 'source_generation': 'prod-v4',
            'candidate_commit': None, 'accounts_source_sha': None,
            'playbooks_runner_sha': None, 'lifecycle_test_source_path': None,
            'run_id': None, 'target_ip': None,
            'postgres_image_digest': None, 'integration_binary_path': None,
            'integration_binary_sha256': None, 'billing_sql_path': None,
            'billing_sql_sha256': None, 'lifecycle_sql_path': None,
            'lifecycle_sql_sha256': None,
            'verified_inventory': None, 'target_cmdb_instance_id': None,
            'target_postgres_container': None, 'protected_vars_file': None,
            'checkpoint': None, 'public_route_expectations_file': None,
            'schema_version': None, 'schema_sha256': None,
            'billing_schema_sha256': None, 'source_archive_sha256': None,
            'source_restore_receipt': None, 'source_writer_fence_evidence': None,
        }
        values.update(overrides)
        return type('Args', (), values)()

    def complete_args(self, routes_file):
        return self.args(
            candidate_commit='a' * 40, accounts_source_sha='b' * 40,
            playbooks_runner_sha='1' * 40,
            lifecycle_test_source_path='internal/migrate/lifecycle_upgrade_postgres_test.go',
            run_id='v5-fixture-run', target_ip='192.0.2.5',
            postgres_image_digest='ghcr.io/example/postgres@sha256:' + 'c' * 64,
            integration_binary_path='/tmp/v5-fixtures/tool',
            integration_binary_sha256='d' * 64,
            billing_sql_path='/tmp/v5-fixtures/billing.sql',
            billing_sql_sha256='e' * 64,
            lifecycle_sql_path='/tmp/v5-fixtures/lifecycle.sql',
            lifecycle_sql_sha256='f' * 64, verified_inventory='/tmp/v5-fixtures/inventory.ini',
            target_cmdb_instance_id='cmdb-v5-fixture',
            target_postgres_container='web-saas-postgresql-v5-fixture',
            protected_vars_file='/tmp/v5-fixtures/vars.json',
            checkpoint='/tmp/prod-v5/fresh-native-v5-r1',
            schema_version='2026102301', schema_sha256='2' * 64,
            billing_schema_sha256='3' * 64, source_archive_sha256='4' * 64,
            public_route_expectations_file=str(routes_file),
            source_restore_receipt='/tmp/v5-fixtures/source-restore.json',
            source_writer_fence_evidence='/tmp/v5-fixtures/writer-fence.json')

    def test_default_batch_is_blocked_and_reports_exact_missing_inputs(self):
        batch = planner.build_batch(self.args())
        self.assertEqual(batch['status'], 'BLOCKED')
        self.assertEqual(batch['target_generation'], 'prod-v5')
        self.assertEqual(batch['source_generation'], 'prod-v4')
        self.assertEqual(len(batch['cases']), 16)
        self.assertEqual(set(batch['missing_required_inputs']), {
            'candidate_gitops_sha', 'playbooks_runner_sha', 'accounts_source_sha',
            'lifecycle_test_source_path', 'run_id', 'target_ip', 'postgres_image_digest',
            'integration_binary_path', 'integration_binary_sha256', 'billing_sql_path',
            'billing_sql_sha256', 'lifecycle_sql_path', 'lifecycle_sql_sha256',
            'verified_inventory', 'target_cmdb_instance_id', 'target_postgres_container',
            'protected_vars_file', 'checkpoint',
            'schema_version', 'schema_sha256', 'billing_schema_sha256',
            'source_archive_sha256',
            'public_route_expectations_file', 'source_restore_receipt',
            'source_writer_fence_evidence'})

    def test_committed_manifest_template_matches_default_planner_output(self):
        template = ROOT / 'docs/cases/runbooks/v5-generation-regression-batch.template.json'
        self.assertEqual(json.loads(template.read_text()), planner.build_batch(self.args()))

    def test_fixture_routes_and_pins_keep_unresolved_writer_gate_blocked(self):
        hosts = planner.PUBLIC_HOSTS
        routes = {host: ['192.0.2.10'] for host in hosts}
        with tempfile.TemporaryDirectory() as temporary:
            route_file = Path(temporary) / 'declared-routes.json'
            route_file.write_text(json.dumps(routes))
            batch = planner.build_batch(self.complete_args(route_file))
        self.assertEqual(batch['status'], 'BLOCKED')
        self.assertEqual(batch['missing_required_inputs'], [])
        self.assertEqual(batch['unresolved_case_inputs'], [
            'writer-ownership executable case path and immutable pin'])
        self.assertEqual(batch['public_route_expectations'], routes)
        serialized = json.dumps(batch)
        self.assertNotIn('/tmp/v5-fixtures/', serialized)
        self.assertEqual(batch['historical_playbooks_case_pin'], 'e2b584716fd6770f64d2ba2a7c4137fa4117c7ae')
        self.assertEqual(len(batch['historical_runner_sha256']), 2)
        self.assertEqual(batch['invocations'][0]['case_ids'], [row[0] for row in planner.HOST_CASES])
        self.assertEqual(batch['invocations'][1]['case_ids'], [row[0] for row in planner.POSTGRES_CASES])
        self.assertEqual(batch['cases'][12]['accounts_test']['source_path'],
                         'internal/migrate/lifecycle_upgrade_postgres_test.go')
        schema_case = next(case for case in batch['cases'] if case['case_id'] == 'schema-data-protection')
        self.assertEqual(schema_case['historical_candidate_pins'][0]['candidate_gitops_sha'],
                         'b5987fefc5e591e0fdba33c684ca83766a16ff37')
        self.assertIsNone(schema_case['historical_candidate_pins'][1]['candidate_gitops_sha'])
        self.assertIsNone(batch['cases'][10]['historical']['accounts_source_sha'])
        self.assertFalse(batch['execution_boundary']['source_mutations'])
        self.assertFalse(batch['execution_boundary']['existing_container_reuse'])

    def test_postgres_cases_keep_real_selectors_and_flag_unresolved_source_path(self):
        cases = {row['case_id']: row for row in [planner._postgres_case(value) for value in planner.POSTGRES_CASES]}
        self.assertEqual(cases['migration-concurrency-retry']['accounts_test']['function'],
                         'TestControlledMigratePostgresIntegration')
        self.assertEqual(cases['migration-concurrency-retry']['accounts_test']['source_path'],
                         'internal/migrate/controlled_test.go')
        lifecycle = cases['lifecycle-migration-rollback']['accounts_test']
        self.assertIsNone(lifecycle['source_path'])
        self.assertEqual(lifecycle['source_path_status'], 'unresolved in available Accounts checkout')
        self.assertEqual(cases['native-initialization']['historical']['evidence'],
                         cases['usage-merge-conflicts']['historical']['evidence'])

    def test_source_and_target_must_be_adjacent_and_routes_cannot_be_implicit(self):
        with self.assertRaisesRegex(ValueError, 'immediately preceding'):
            planner.build_batch(self.args(target_generation='prod-v6', source_generation='prod-v4'))
        with self.assertRaisesRegex(ValueError, 'exactly the five public hostnames'):
            with tempfile.TemporaryDirectory() as temporary:
                route_file = Path(temporary) / 'routes.json'
                route_file.write_text(json.dumps({'xworktech.com': ['192.0.2.1']}))
                planner.build_batch(self.args(public_route_expectations_file=str(route_file)))

    def test_host_runner_requires_declared_routes_and_has_no_v3_public_default(self):
        self.assertEqual(runner.validate_public_route_expectations(
            {host: ['192.0.2.2'] for host in runner.PUBLIC_HOSTS}),
            {host: ['192.0.2.2'] for host in runner.PUBLIC_HOSTS})
        with self.assertRaisesRegex(ValueError, 'exactly every public host'):
            runner.validate_public_route_expectations(None)
        with self.assertRaisesRegex(ValueError, 'IPv4'):
            runner.validate_public_route_expectations(
                {host: ['not-an-ip'] if host == runner.PUBLIC_HOSTS[0] else ['192.0.2.2']
                 for host in runner.PUBLIC_HOSTS})
        public_case = dict(planner._host_case(planner.HOST_CASES[1]))
        self.assertIn('supplied stage declaration', public_case['expected'])
        self.assertNotIn('V3', public_case['expected'])


if __name__ == '__main__':
    unittest.main()
