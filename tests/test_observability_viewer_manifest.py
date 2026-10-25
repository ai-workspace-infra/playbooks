import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.parse

import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('viewer_manifest', ROOT / 'roles/observability_audit_session/files/verify_manifest.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def fixture():
    dashboards = []
    models = {}
    for uid in sorted(m.UIDS):
        expr = 'sum(node_cpu_seconds_total{cluster=~"${cluster:regex}",environment=~"${environment:regex}",instance=~"$instance"})'
        query = 'label_values(node_uname_info{cluster=~"${cluster:regex}",environment=~"${environment:regex}"},instance)'
        model = {'uid': uid, 'templating': {'list': [{'name': 'instance', 'type': 'query', 'query': query}]},
                 'panels': [{'id': 1, 'targets': [{'refId': 'A', 'expr': expr}]}]}
        models[uid] = model
        dashboards.append({'uid': uid, 'families': ['node_cpu_seconds_total'], 'variables_in_order': [{'name': 'instance', 'query': query}],
                           'targets': [{'panel_id': 1, 'ref_id': 'A', 'hidden': False, 'expr': expr, 'families': ['node_cpu_seconds_total']}]})
    return {'schema_version': 1, 'dashboards': dashboards, '_sha256': 'fixture'}, models


class ViewerManifest(unittest.TestCase):
    def setUp(self):
        self.plan, self.models = fixture()
        self.args = SimpleNamespace(cluster='test.cluster', environment='uat', datasource_uid='victoriametrics', expected_node=['node1'])
        self.paths = []
        self.mode = 'ok'

    def get(self, path):
        self.paths.append(path)
        self.assertTrue(path.startswith(('/api/dashboards/uid/', '/api/datasources/proxy/uid/victoriametrics/api/v1/query?')))
        if path.startswith('/api/dashboards/'):
            return 200, {'dashboard': self.models[path.rsplit('/', 1)[1]]}
        expr = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)['query'][0]
        if self.mode == 'semantic_error':
            return 200, {'status': 'error', 'error': 'private backend text must not escape'}
        rows = [{'metric': {'node': 'node1'}, 'value': [0, '10']}]
        if expr.startswith('count by(instance)'):
            rows[0]['metric']['instance'] = 'node1'
        if self.mode == 'missing_family' and expr.startswith('time()'):
            rows = []
        if self.mode == 'missing_node' and expr.startswith('time()'):
            rows[0]['metric']['node'] = 'other'
        if self.mode == 'stale' and expr.startswith('time()'):
            rows[0]['value'][1] = '999'
        if self.mode == 'empty_target' and expr.startswith('sum('):
            rows = []
        if self.mode == 'nonfinite':
            rows[0]['value'][1] = 'NaN'
        return 200, {'status': 'success', 'data': {'resultType': 'vector', 'result': rows}}

    def run_manifest(self):
        return m.execute(self.plan, self.args, self.get)

    def test_valid_get_only_sanitized_results(self):
        result = self.run_manifest()
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['query_count'], 9)
        self.assertNotIn('node1', json.dumps(result))
        self.assertNotIn('expr"', json.dumps(result))
        for path in self.paths:
            self.assertNotIn('$', urllib.parse.unquote(path))

    def test_semantic_http_200_error_is_fail_without_raw_error(self):
        self.mode = 'semantic_error'
        result = self.run_manifest()
        self.assertEqual(result['status'], 'FAIL')
        self.assertNotIn('private backend', json.dumps(result))

    def test_required_missing_family_fails_conditional_is_not_run(self):
        self.mode = 'missing_family'
        self.assertEqual(self.run_manifest()['status'], 'FAIL')
        self.plan['conditional_data'] = {'node_cpu_seconds_total': 'test conditional'}
        self.assertEqual(self.run_manifest()['status'], 'NOT_RUN')

    def test_missing_external_node_and_stale_family_fail(self):
        for mode in ('missing_node', 'stale', 'nonfinite'):
            self.mode = mode
            self.assertEqual(self.run_manifest()['status'], 'FAIL')

    def test_empty_target_is_not_healthy_zero(self):
        self.mode = 'empty_target'
        self.assertEqual(self.run_manifest()['status'], 'NOT_RUN')

    def test_live_contract_mismatch_blocks_queries(self):
        for model in self.models.values():
            model['panels'][0]['targets'][0]['expr'] = 'up'
        result = self.run_manifest()
        self.assertEqual(result['status'], 'FAIL')
        self.assertEqual(result['query_count'], 0)

    def test_unknown_macro_skips_query_and_hidden_target_excluded(self):
        for model, plan in zip(self.models.values(), self.plan['dashboards']):
            expr = 'node_cpu_seconds_total{instance="$unknown"}'
            model['panels'][0]['targets'][0]['expr'] = expr
            plan['targets'][0]['expr'] = expr
        self.assertEqual(self.run_manifest()['status'], 'NOT_RUN')
        for model, plan in zip(self.models.values(), self.plan['dashboards']):
            model['panels'][0]['targets'][0]['hide'] = True
            plan['targets'][0]['hidden'] = True
        self.assertEqual(self.run_manifest()['status'], 'PASS')

    def test_macro_escaping_and_unsupported_format(self):
        value = m.regex_values(['test.cluster', 'node1'])
        self.assertIn('\\\\.', value)
        self.assertEqual(m.expand('$__rate_interval ${cluster:regex}', {'__rate_interval': '3m', 'cluster': value}), '3m ' + value)
        with self.assertRaises(ValueError):
            m.expand('${cluster:raw}', {'cluster': value})

    def test_exact_node_self_fd_family_ownership_and_node_coverage(self):
        node = next(d for d in self.plan['dashboards'] if d['uid'] == 'StarsL-JOB-node')
        node['families'] += ['process_open_fds', 'process_max_fds']
        self.assertEqual(m.metric_job('process_open_fds', 'StarsL-JOB-node'), 'node-exporter')
        result = self.run_manifest()
        self.assertEqual(result['status'], 'PASS')
        queries = [urllib.parse.parse_qs(urllib.parse.urlsplit(path).query).get('query', [''])[0] for path in self.paths]
        for name in m.NODE_SELF_PROCESS_METRICS:
            expressions = [expr for expr in queries if name in expr]
            self.assertEqual(len(expressions), 1)
            self.assertIn('job="node-exporter"', expressions[0])
            self.assertNotIn('job="process-exporter"', expressions[0])
        self.mode = 'missing_node'
        self.assertEqual(self.run_manifest()['status'], 'FAIL')

    def test_self_fd_metrics_rejected_outside_node_dashboard(self):
        for name in m.NODE_SELF_PROCESS_METRICS:
            with self.assertRaises(ValueError):
                m.metric_job(name, 'process-exporter-with-tree')
        with self.assertRaises(ValueError):
            m.metric_job('process_arbitrary_metric', 'StarsL-JOB-node')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp).resolve() / 'manifest.yaml'
            node = next(d for d in self.plan['dashboards'] if d['uid'] == 'StarsL-JOB-node')
            node['families'].append('process_max_fds')
            path.write_text(yaml.safe_dump(self.plan))
            self.assertEqual(len(m.load_manifest(path)['dashboards']), 3)
            process = next(d for d in self.plan['dashboards'] if d['uid'] == 'process-exporter-with-tree')
            process['families'].append('process_max_fds')
            path.write_text(yaml.safe_dump(self.plan))
            with self.assertRaises(ValueError):
                m.load_manifest(path)


    def test_actual_interval_type_and_query_result_variables(self):
        for model, plan in zip(self.models.values(), self.plan['dashboards']):
            model['templating']['list'].insert(0, {'name': 'interval', 'type': 'interval', 'query': '3m'})
            query = 'query_result(count(node_uname_info{cluster="test.cluster",environment="uat"}))'
            model['templating']['list'].append({'name': 'total', 'type': 'query', 'query': query})
            plan['variables_in_order'].append({'name': 'total', 'query': query})
            expr = 'rate(node_cpu_seconds_total{cluster=~"${cluster:regex}",environment=~"${environment:regex}"}[$interval])'
            model['panels'][0]['targets'][0]['expr'] = expr
            plan['targets'][0]['expr'] = expr
        result = self.run_manifest()
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['query_count'], 12)


    def test_legacy_unscoped_query_and_dropped_metric_never_pass(self):
        for model, plan in zip(self.models.values(), self.plan['dashboards']):
            expr = 'sum(node_cpu_seconds_total{instance=~"$instance"})'
            model['panels'][0]['targets'][0]['expr'] = expr
            plan['targets'][0]['expr'] = expr
        result = self.run_manifest()
        self.assertEqual(result['status'], 'NOT_RUN')
        self.assertTrue(all(d['unscoped_live_target_count'] == 1 for d in result['dashboards']))
        self.mode = 'missing_family'
        self.plan['not_collected'] = ['node_cpu_seconds_total']
        result = self.run_manifest()
        self.assertEqual(result['status'], 'NOT_RUN')
        self.assertEqual(result['dashboards'][0]['families'][0]['reason'], 'deliberately_excluded_by_nonroot_profile')

    def test_owner_identity_and_permission_gates_precede_extension(self):
        from tests.test_observability_audit_verifier import Fixture, verifier
        for fixture in (Fixture(write=True), Fixture(user_overrides={'isGrafanaAdmin': True})):
            config = SimpleNamespace(base_url='https://grafana.example.test', cluster='cluster-fixture',
                environment='prod', datasource_uid='victoriametrics', expected_node=['node-a', 'node-b'],
                _dashboard_manifest=self.plan)
            with patch.object(verifier.urllib.request, 'build_opener', return_value=fixture):
                report = verifier.verify(config, 'viewer-token-sentinel', {'id': 7, 'orgId': 1, 'role': 'Viewer',
                    'login': 'sa-1-observability-audit-prod', 'name': 'observability-audit-prod'})
            self.assertNotIn('dashboard_manifest', report['checks'])
            self.assertTrue(all(request.get_method() == 'GET' for request in fixture.calls))

    def test_manifest_rejected_before_vault_or_mint(self):
        from tests.test_observability_audit_session import args, session
        credential_reader = Mock()
        with patch.dict(session.os.environ, {'VAULT_ADDR': 'https://vault.example.test'}, clear=True):
            with self.assertRaises(session.SessionError):
                session.run(args(dashboard_query_manifest='relative.yaml'), credential_reader=credential_reader)
        credential_reader.assert_not_called()


    def test_deadline_and_bounded_manifest_preflight(self):
        times = iter([0] + [301] * 20)
        result = m.execute(self.plan, self.args, self.get, clock=lambda: next(times))
        self.assertEqual(result['status'], 'NOT_RUN')
        self.assertEqual(result['query_count'], 0)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp).resolve() / 'manifest.yaml'
            path.write_text(yaml.safe_dump(self.plan))
            self.assertEqual(len(m.load_manifest(path)['dashboards']), 3)
            bad = copy.deepcopy(self.plan)
            bad['dashboards'][0]['families'] = ['arbitrary_secret_metric']
            path.write_text(yaml.safe_dump(bad))
            with self.assertRaises(ValueError):
                m.load_manifest(path)
            path.write_text('invalid: [')
            with self.assertRaises(ValueError):
                m.load_manifest(path)
            with self.assertRaises(ValueError):
                m.load_manifest('relative.yaml')


if __name__ == '__main__':
    unittest.main()
