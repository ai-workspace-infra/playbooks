import json
from pathlib import Path
import unittest

FILES = Path(__file__).resolve().parents[1] / 'roles/docker/observability-server/files'


class ComprehensiveDashboardTests(unittest.TestCase):
    def test_three_sources_share_one_dashboard(self):
        dashboard = json.loads((FILES / 'xconnect-user-connection-diagnostics.json').read_text())
        self.assertEqual(dashboard['uid'], 'xconnect-user-connection-diagnostics')
        self.assertEqual(dashboard['title'], 'XConnect 综合监控')
        panels = dashboard['panels']
        self.assertEqual(len({p['id'] for p in panels}), len(panels))
        diagnostic = next(p for p in panels if p['id'] == 1)
        self.assertIn('/grafana/a/aiworkspace-xconnectuserdiagnostics-app', diagnostic['options']['content'])
        self.assertEqual(panels[0]['title'], '1 · Xray 流量与在线状态')
        self.assertEqual(panels[-1]['id'], 1)
        expressions = '\n'.join(t.get('expr', '') for p in panels for t in p.get('targets', []))
        for metric in ['xray_up', 'xray_traffic_downlink_bytes_total', 'xhttp_events_total', 'caddy_config_last_reload_successful']:
            self.assertIn(metric, expressions)
        self.assertFalse({'graph', 'singlestat', 'table-old'} & {p['type'] for p in panels})
        variables = {v['name']: v for v in dashboard['templating']['list']}
        self.assertTrue(variables['user']['skipUrlSync'])
        self.assertIn('caddy_config_last_reload_successful', variables['instance']['query'])
        for i, left in enumerate(panels):
            a = left['gridPos']
            for right in panels[i+1:]:
                b = right['gridPos']
                self.assertFalse(a['x'] < b['x']+b['w'] and b['x'] < a['x']+a['w'] and a['y'] < b['y']+b['h'] and b['y'] < a['y']+a['h'])

    def test_legacy_bookmarks_are_navigation_only(self):
        for name in ['dashboard.json', 'xhttp-edge-reliability-dashboard.json']:
            dashboard = json.loads((FILES / name).read_text())
            self.assertEqual(len(dashboard['panels']), 1)
            self.assertIn('/grafana/d/xconnect-user-connection-diagnostics/', dashboard['panels'][0]['options']['content'])
