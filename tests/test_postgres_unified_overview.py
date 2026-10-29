import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = ROOT / 'roles/docker/observability-server/files'
LABELS = ['environment', 'cluster', 'service', 'instance', 'datname']


class UnifiedPostgresDashboardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dashboard = json.loads((FILES / 'postgres-unified-overview.json').read_text())

    def test_identity_and_source_dashboards_retained(self):
        self.assertEqual(self.dashboard['uid'], 'postgres-unified-overview')
        self.assertEqual(json.loads((FILES / 'postgres-community-overview.json').read_text())['uid'], 'postgres-community-overview')
        self.assertEqual(json.loads((FILES / 'postgres-exporter-dashboard.json').read_text())['uid'], 'postgres-exporter-overview')
        ids = [p['id'] for p in self.dashboard['panels']]
        self.assertEqual(len(ids), len(set(ids)))

    def test_cascade_includes_every_parent(self):
        variables = self.dashboard['templating']['list']
        self.assertEqual([v['name'] for v in variables], ['datasource'] + LABELS)
        for index, var in enumerate(variables[1:]):
            query = var['query']['query']
            self.assertEqual(var['datasource']['uid'], '$datasource')
            for label in LABELS[:index]:
                self.assertIn(f'{label}=~"${{{label}:regex}}"', query)
                self.assertIn(f'{label}!=""', query)
            self.assertIn(f'{LABELS[index]}!=""', query)

    def test_all_metrics_are_scoped_and_missing_data_is_not_filled(self):
        seen = set()
        for panel in self.dashboard['panels']:
            for target in panel.get('targets', []):
                expr = target['expr']
                self.assertNotRegex(expr, r'\bor\s+(?:on\s*\([^)]*\)\s*)?vector\s*\(\s*0\s*\)')
                metrics = re.findall(r'(pg_[a-zA-Z0-9_:]+)\{((?:\$\{[^}]*\}|[^}])*)\}', expr)
                self.assertTrue(metrics, expr)
                self.assertEqual(len(metrics), len(re.findall(r'\bpg_[a-zA-Z0-9_:]+', expr)))
                for metric, selector in metrics:
                    seen.add(metric)
                    for label in LABELS if metric.startswith('pg_stat_database_') or metric == 'pg_database_size_bytes' else LABELS[:-1]:
                        self.assertIn(f'{label}=~"${{{label}:regex}}"', selector, expr)
                        self.assertIn(f'{label}!=""', selector, expr)
                    if metric == 'pg_up' or metric.startswith(('pg_stat_bgwriter_', 'pg_stat_checkpointer_')):
                        self.assertNotIn('datname=', selector)
        self.assertTrue({'pg_up', 'pg_database_size_bytes', 'pg_stat_database_xact_commit', 'pg_stat_database_xact_rollback', 'pg_stat_database_deadlocks', 'pg_stat_database_tup_fetched', 'pg_stat_checkpointer_buffers_written_total'} <= seen)

    def test_caddy_style_sections_and_real_metric_heatmaps(self):
        panels = self.dashboard['panels']
        self.assertEqual([p['title'] for p in panels if p['type'] == 'row'],
                         ['Summary', 'Detailed metrics', 'Heatmaps'])
        heatmaps = [p for p in panels if p['type'] == 'heatmap']
        self.assertEqual(len(heatmaps), 2)
        for panel, metric in zip(heatmaps, ['pg_stat_database_numbackends', 'pg_database_size_bytes']):
            self.assertTrue(panel['options']['calculate'])
            self.assertIn(metric, panel['targets'][0]['expr'])
            self.assertEqual(panel['targets'][0]['format'], 'time_series')
            self.assertIn('sample count', panel['description'])
        self.assertEqual(len([p for p in panels if p['type'] == 'stat']), 6)

    def test_layout_has_no_overlaps(self):
        cells = set()
        for panel in self.dashboard['panels']:
            p = panel['gridPos']
            self.assertLessEqual(p['x'] + p['w'], 24)
            for x in range(p['x'], p['x'] + p['w']):
                for y in range(p['y'], p['y'] + p['h']):
                    self.assertNotIn((x, y), cells, panel['title'])
                    cells.add((x, y))

if __name__ == '__main__':
    unittest.main()
