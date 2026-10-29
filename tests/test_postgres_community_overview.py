import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / 'roles/docker/observability-server/files/postgres-community-overview.json'
LABELS = ['environment', 'cluster', 'service', 'instance', 'datname']

class CommunityPostgresDashboardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dashboard = json.loads(DASH.read_text())

    def test_cascade_filters_every_parent_and_excludes_missing_labels(self):
        variables = self.dashboard['templating']['list']
        self.assertEqual([v['name'] for v in variables], ['datasource'] + LABELS)
        for index, var in enumerate(variables[1:]):
            query = var['query']['query']
            self.assertEqual(var['datasource']['uid'], '$datasource')
            for label in LABELS[:index]:
                self.assertIn(f'{label}=~"${{{label}:regex}}"', query)
                self.assertIn(f'{label}!=""', query)
            self.assertIn(f'{LABELS[index]}!=""', query)

    def test_every_metric_selector_is_scoped(self):
        seen = 0
        for panel in self.dashboard['panels']:
            for target in panel.get('targets', []):
                expr = target.get('expr', '')
                metrics = re.findall(r'(pg_[a-zA-Z0-9_:]+)\{((?:\$\{[^}]*\}|[^}])*)\}', expr)
                self.assertTrue(metrics, expr)
                self.assertEqual(len(metrics), len(re.findall(r'\bpg_[a-zA-Z0-9_:]+', expr)))
                for metric, selector in metrics:
                    seen += 1
                    for label in LABELS if metric.startswith('pg_stat_database_') else LABELS[:-1]:
                        self.assertIn(f'{label}=~"${{{label}:regex}}"', selector, expr)
                        self.assertIn(f'{label}!=""', selector, expr)
        self.assertGreater(seen, 10)

    def test_pg17_instance_scope_and_checkpointer_are_explicit(self):
        panel = next(p for p in self.dashboard['panels'] if p.get('title') == 'Buffers (instance-wide)')
        self.assertIn('database selection does not apply', panel['description'])
        self.assertTrue(any('pg_stat_checkpointer_buffers_written_total' in t['expr'] for t in panel['targets']))
        self.assertEqual(self.dashboard['uid'], 'postgres-community-overview')

if __name__ == '__main__':
    unittest.main()
