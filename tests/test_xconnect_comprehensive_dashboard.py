import json
from pathlib import Path
import unittest

FILES = Path(__file__).resolve().parents[1] / 'roles/docker/observability-server/files'


class ComprehensiveDashboardTests(unittest.TestCase):
    def test_three_sources_share_one_dashboard(self):
        dashboard = json.loads((FILES / 'xconnect-comprehensive.json').read_text())
        self.assertEqual(dashboard['uid'], 'xconnect-comprehensive')
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

    def test_existing_dashboards_keep_their_content_and_uids(self):
        for name, uid, metric in [
            ('dashboard.json', 'begqoward2epsf', 'xray_'),
            ('xhttp-edge-reliability-dashboard.json', 'xhttp-edge-reliability', 'xhttp_events_total'),
        ]:
            dashboard = json.loads((FILES / name).read_text())
            self.assertEqual(dashboard['uid'], uid)
            self.assertGreater(len(dashboard['panels']), 1)
            self.assertIn(metric, json.dumps(dashboard))
            self.assertNotIn('旧书签保留', json.dumps(dashboard, ensure_ascii=False))
            self.assertNotIn('已合并', dashboard['title'])
            self.assertFalse({'graph', 'singlestat', 'table-old'} & {p['type'] for p in dashboard['panels']})
        diagnostics = json.loads((FILES / 'xconnect-user-connection-diagnostics.json').read_text())
        self.assertEqual(diagnostics['uid'], 'xconnect-user-connection-diagnostics')
        self.assertEqual(diagnostics['title'], 'XConnect 用户慢连接诊断')
        self.assertIn('/grafana/a/aiworkspace-xconnectuserdiagnostics-app', diagnostics['panels'][0]['options']['content'])

    def test_all_dashboard_uids_are_unique(self):
        dashboards = [json.loads(path.read_text()) for path in FILES.glob('*.json')]
        uids = [d['uid'] for d in dashboards if d.get('uid')]
        self.assertEqual(len(uids), len(set(uids)))
        for dashboard in dashboards:
            self.assertNotIn('统一监控入口', [p.get('title') for p in dashboard.get('panels', [])])
