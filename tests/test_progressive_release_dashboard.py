import json
import unittest
from pathlib import Path

DASHBOARD = Path(__file__).resolve().parents[1] / 'roles/docker/observability-server/files/progressive-release-validation.json'


class ProgressiveReleaseDashboardTest(unittest.TestCase):
    def test_probe_matrix_and_no_data_contract(self):
        doc = json.loads(DASHBOARD.read_text())
        panels = doc['panels']
        expected = {
            'Console · /healthz', 'Console · /login', 'Console · /panel/agent',
            'Accounts · /healthz', 'Accounts · /readyz', 'Accounts · /api/ping',
            'Accounts · /api/billing/plans', 'Billing · /healthz',
            'Billing · /readyz', 'Billing · /api/ping',
        }
        self.assertTrue(expected.issubset({panel['title'] for panel in panels}))
        matrix = [panel for panel in panels if panel['title'] in expected]
        self.assertEqual(len(matrix), 10)
        for panel in matrix:
            self.assertEqual(panel['fieldConfig']['defaults']['noValue'], 'NO DATA')
            self.assertIn('probe_success{', panel['targets'][0]['expr'])
            self.assertNotIn('vector(0)', panel['targets'][0]['expr'])
        gate = next(panel for panel in panels if panel['title'] == 'Probe gate · all 10 checks')
        self.assertIn('count(probe_success', gate['targets'][0]['expr'])
        self.assertIn('== 10', gate['targets'][0]['expr'])
        self.assertNotIn('vector(0)', gate['targets'][0]['expr'])

    def test_identity_and_datasource_are_parameters(self):
        doc = json.loads(DASHBOARD.read_text())
        self.assertEqual(doc['uid'], 'progressive-release-validation')
        self.assertEqual({v['name'] for v in doc['templating']['list']}, {'DS_METRICS', 'environment', 'cluster', 'namespace'})
        ids = [p['id'] for p in doc['panels']]
        self.assertEqual(len(ids), len(set(ids)))


if __name__ == '__main__':
    unittest.main()
