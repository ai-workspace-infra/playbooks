import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('grafana_config', ROOT / 'roles/platform/grafana_config/files/grafana_config.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

def fixture():
    return {'source': 'https://source.example.test/grafana', 'health': {'version': '13.2.3'}, 'folders': [{'uid': 'alerts', 'title': 'Alerts'}], 'datasources': [{'uid': 'metrics', 'name': 'Metrics', 'type': 'prometheus', 'url': 'http://old-metrics:8428', 'secureJsonFields': {}}], 'dashboards': [{'dashboard': {'id': 7, 'version': 12, 'uid': 'overview', 'title': 'Overview', 'panels': [{'id': 1, 'type': 'stat', 'options': {'textMode': 'value'}}], 'templating': {'list': [{'name': 'node', 'type': 'query', 'query': 'label_values(up, instance)', 'current': {'text': 'old-node'}, 'options': [{'text': 'old-node'}]}]}, 'links': [{'url': 'https://source.example.test/grafana/d/overview'}]}, 'meta': {'folderUid': ''}}]}

def options():
    return {'prefix': 'fixture-grafana', 'namespace': 'fixture', 'target_url': 'https://target.example.test', 'datasource_overrides': {'metrics': {'url': 'http://127.0.0.1:8428'}}, 'reserved_uids': ['target-only']}

class Bundle(unittest.TestCase):
    def test_deterministic_three_replays_and_source_identity(self):
        source = fixture(); before = json.dumps(source, sort_keys=True)
        with tempfile.TemporaryDirectory() as directory:
            resources, inventory, checksum = m.build(source, options())
            self.assertTrue(m.write_bundle(resources, directory, True))
            for _ in range(3):
                again, report, sha = m.build(source, options())
                self.assertEqual(sha, checksum); self.assertEqual(report, inventory)
                self.assertEqual(m.write_bundle(again, directory, True), [])
        self.assertEqual(json.dumps(source, sort_keys=True), before)

    def test_uid_variables_navigation_and_empty_folder(self):
        resources, inventory, _ = m.build(fixture(), options())
        provider = next(x for x in resources.values() if x.get('kind') == 'ConfigMap' and 'overview.json' in x['data'])
        d = json.loads(provider['data']['overview.json'])
        self.assertEqual(d['uid'], 'overview'); self.assertNotIn('id', d)
        self.assertEqual(d['templating']['list'][0]['query'], 'label_values(up, instance)')
        self.assertEqual(d['templating']['list'][0]['current'], {})
        self.assertEqual(d['links'][0]['url'], 'https://target.example.test/d/overview')
        self.assertTrue(any('README.txt' in x.get('data', {}) for x in resources.values()))
        self.assertEqual(inventory['historical_data_migration'], 'NOT_RUN')
        self.assertEqual(inventory['production_alert_channels'], 'NOT_IMPORTED')

    def test_secrets_need_target_environment_references(self):
        source = fixture(); source['datasources'][0]['secureJsonFields'] = {'password': True}
        with self.assertRaises(ValueError):m.build(source, options())
        o = options(); o['datasource_overrides']['metrics']['secureJsonData'] = {'password': 'plaintext-fixture'}
        with self.assertRaises(ValueError):m.build(source, o)
        o['datasource_overrides']['metrics']['secureJsonData']['password'] = '${TARGET_PASSWORD}'
        m.build(source, o)

    def test_reserved_uid_missing_endpoint_and_library_fail_closed(self):
        source = fixture(); source['dashboards'][0]['dashboard']['uid'] = 'target-only'
        with self.assertRaises(ValueError):m.build(source, options())
        o = options(); o['datasource_overrides'] = {}
        with self.assertRaises(ValueError):m.build(fixture(), o)
        source = fixture(); source['dashboards'][0]['dashboard']['panels'][0]['libraryPanel'] = {'uid': 'missing'}
        with self.assertRaises(ValueError):m.build(source, options())

    def test_source_transport_get_only_and_https(self):
        with self.assertRaises(ValueError):m.api_get('http://source.example.test', '/api/health', {})
        with self.assertRaises(ValueError):m.api_get('https://source.example.test', '/api/../alerting', {})
        class Response:
            def __enter__(self):return self
            def __exit__(self, *args):pass
            def read(self):return b'{}'
        with patch.object(m.urllib.request, 'urlopen', return_value=Response()) as call:
            m.api_get('https://source.example.test/grafana', '/api/health', {'token': 'fixture-only'})
            self.assertEqual(call.call_args.args[0].get_method(), 'GET')

    def test_panel_options_are_part_of_verification(self):
        source = fixture(); _, first, _ = m.build(source, options())
        source['dashboards'][0]['dashboard']['panels'][0]['options']['textMode'] = 'name'
        _, second, _ = m.build(source, options())
        self.assertNotEqual(first['dashboards']['overview']['panels_sha256'], second['dashboards']['overview']['panels_sha256'])

if __name__ == '__main__': unittest.main()
