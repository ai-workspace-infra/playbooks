import copy
import importlib.util
import json
import shutil
import tempfile
import yaml
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('host_scope', ROOT / 'roles/platform/grafana_host_dashboard_scope/files/render_dashboards.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class HostScope(unittest.TestCase):
    def model(self, uid):
        return json.loads((ROOT / 'roles/docker/observability-server/files' / m.HOSTS[uid][0]).read_text())

    def test_scope_bare_metric_and_quoted_label(self):
        expr = 'namedprocess_namegroup_memory_bytes{memtype="resident",instance=~"$instance"} / ignoring(memtype) namedprocess_namegroup_num_procs >0'
        result = m.scope_query(expr, 'process-exporter')
        self.assertEqual(result.count(m.SCOPE), 2)
        self.assertIn('memtype="resident"', result)
        self.assertIn('instance=~"$instance"', result)
        self.assertEqual(m.scope_query(result, 'process-exporter'), result)
        quoted = 'node_uname_info{job="node_exporter",nodename="node_not_a_metric",note="x}\\\"y"}'
        adapted = m.scope_query(quoted, 'node-exporter')
        self.assertEqual(adapted.count(m.SCOPE), 1)
        self.assertIn('nodename="node_not_a_metric"', adapted)
        self.assertIn('note="x}\\\"y"', adapted)

    def test_scope_replace_old_dimensions_and_job(self):
        result = m.scope_query('rate(node_cpu_seconds_total{cluster="old",environment="old",job=~"$job",mode="idle"}[3m])', 'node-exporter')
        self.assertNotIn('old', result)
        self.assertNotIn('$job', result)
        self.assertEqual(result.count('cluster='), 1)
        self.assertIn('mode="idle"', result)

    def test_all_queries_scoped_idempotent_and_original_ids_retained(self):
        for uid in m.HOSTS:
            model = self.model(uid)
            result = m.adapt(model)
            self.assertEqual(result, model)  # Committed output is reproducible.
            self.assertEqual(m.adapt(result), result)
            self.assertEqual([p['id'] for p in m.walk_panels(result['panels'])],
                             [p['id'] for p in m.walk_panels(model['panels'])])
            queries = []
            for panel in m.walk_panels(result['panels']):
                queries += [target['expr'] for target in panel.get('targets', []) if target.get('expr')]
            for variable in result['templating']['list']:
                if variable['type'] == 'query' and variable['name'] not in ('cluster', 'environment'):
                    query = variable['query']
                    queries.append(query['query'] if isinstance(query, dict) else query)
            for expr in queries:
                for name, start, end in m.tokens(expr):
                    if name.startswith(('node_', 'namedprocess_')):
                        stop = m.selector_end(expr, end)
                        selector = expr[end:stop + 1]
                        self.assertIn(m.SCOPE, selector, expr)
                        self.assertIn('job="' + m.HOSTS[uid][1] + '"', selector, expr)
            names = [v['name'] for v in result['templating']['list']]
            self.assertLess(names.index('cluster'), names.index('environment'))
            cluster = next(v for v in result['templating']['list'] if v['name'] == 'cluster')
            self.assertNotIn('$environment', cluster['definition'])
            environment = next(v for v in result['templating']['list'] if v['name'] == 'environment')
            self.assertNotIn('${environment:', environment['definition'])

    def test_process_permissions_are_visible_without_synthetic_zero(self):
        model = self.model('process-exporter-with-tree')
        for panel in m.walk_panels(model['panels']):
            if panel['id'] in m.DROPPED:
                self.assertEqual(panel['type'], 'text')
                self.assertEqual(panel['targets'], [])
                self.assertIn('Not collected', panel['options']['content'])
            if panel['id'] in (2, 4):
                self.assertIn('permission-limited', panel['title'])
                self.assertEqual(panel['description'], m.PSS)
                self.assertTrue(panel['targets'])
        copy_model = copy.deepcopy(model)
        copy_model['panels'][0].setdefault('fieldConfig', {}).setdefault('defaults', {})['noValue'] = '0'
        self.assertEqual(m.adapt(copy_model)['panels'][0]['fieldConfig']['defaults']['noValue'], 'No data')

    def test_consumer_preserves_other_models_and_detects_missing_uid(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            source = base / 'producer/roles/docker/observability-server/files'
            source.mkdir(parents=True)
            original_source = ROOT / 'roles/docker/observability-server/files'
            for filename in [v[0] for v in m.HOSTS.values()] + ['starsl-k8s-dashboard.json']:
                shutil.copyfile(original_source / filename, source / filename)
            target = base / 'consumer/services/platform-observability/prod/imported-grafana'
            target.mkdir(parents=True)
            data = {uid + '.json': m.model_text(self.model(uid)) for uid in m.HOSTS}
            unrelated = {str(i) + '.json': json.dumps({'uid': 'other-' + str(i), 'panels': []}) for i in range(16)}
            data.update(unrelated)
            cm = {'kind': 'ConfigMap', 'metadata': {'name': 'source-grafana-general-0'}, 'data': data}
            general = target / 'source-grafana-general-0.yaml'
            general.write_text(yaml.safe_dump(cm))
            values = target / 'source-grafana-values.yaml'
            values.write_text(yaml.safe_dump({'kind': 'ConfigMap', 'metadata': {'name': 'values'},
                'data': {'values.yaml': yaml.safe_dump({'grafana': {'bundleChecksum': 'old'}})}}))
            stars = {'kind': 'ConfigMap', 'metadata': {'name': 'stars'}, 'data': {'stars.json': (source / 'starsl-k8s-dashboard.json').read_text()}}
            (target / 'starsl-k8s-dashboard.yaml').write_text(yaml.safe_dump(stars))
            result = m.render(base / 'producer', base / 'consumer', base / 'manifest.yaml')
            adapted = yaml.safe_load(result[general])
            self.assertEqual({k: adapted['data'][k] for k in unrelated}, unrelated)
            for path, body in result.items():
                path.write_text(body)
            self.assertEqual(m.render(base / 'producer', base / 'consumer', base / 'manifest.yaml'), result)
            del adapted['data']['StarsL-JOB-node.json']
            general.write_text(yaml.safe_dump(adapted))
            with self.assertRaisesRegex(ValueError, 'Missing expected'):
                m.render(base / 'producer', base / 'consumer')

    def test_single_quote_and_backtick_values_are_preserved(self):
        expr = "node_uname_info{note='x,y}',nodename=`a,b`}"
        result = m.scope_query(expr, 'node-exporter')
        self.assertIn("note='x,y}'", result)
        self.assertIn('nodename=`a,b`', result)
        self.assertEqual(m.scope_query(result, 'node-exporter'), result)


    def test_p99_raw_queries_do_not_require_recording_rules(self):
        node = self.model('StarsL-JOB-node')
        panel = next(p for p in m.walk_panels(node['panels']) if p['id'] == 200)
        queries = {t['refId']: t['expr'] for t in panel['targets']}
        for ref in ('B', 'C'):
            self.assertEqual(queries[ref], m.node_p99_queries()[ref])
            self.assertIn('[7d:5m]', queries[ref])
            self.assertIn('instance,cluster,environment', queries[ref])
            self.assertIn(m.SCOPE, queries[ref])
        all_expr = ' '.join(t.get('expr', '') for p in m.walk_panels(node['panels']) for t in p.get('targets', []))
        self.assertNotIn('cpu:', all_expr)
        self.assertNotIn('mem:', all_expr)
        self.assertIn('does not prove seven days', panel['description'])
        self.assertEqual(panel['transformations'][0]['options']['byField'], 'instance')


    def test_manifest_all_targets_families_and_no_string_false_positive(self):
        models = [self.model(uid) for uid in m.HOSTS]
        models.append(json.loads((ROOT / 'roles/docker/observability-server/files/starsl-k8s-dashboard.json').read_text()))
        manifest = m.manifest(models)
        self.assertEqual(len(manifest['dashboards']), 3)
        stars = next(d for d in manifest['dashboards'] if d['uid'] == 'StarsL_cn_K8S')
        self.assertEqual(len(stars['targets']), 126)
        self.assertIn('kube_namespace_created', stars['families'])
        node = next(d for d in manifest['dashboards'] if d['uid'] == 'StarsL-JOB-node')
        self.assertIn('node_network_info', node['families'])
        self.assertNotIn('node_exporter', node['families'])
        process = next(d for d in manifest['dashboards'] if d['uid'] == 'process-exporter-with-tree')
        self.assertEqual(len(process['targets']), 12)
        self.assertFalse(set(manifest['not_collected']) & set(process['families']))
        self.assertIn('kubelet_volume_stats_used_bytes', manifest['conditional_data'])


if __name__ == '__main__':
    unittest.main()
