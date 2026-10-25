"""Real PromQL semantics against a disposable loopback-only VictoriaMetrics fixture."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import time
import unittest
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
IMAGE = 'victoriametrics/victoria-metrics@sha256:91d0456cc064e195869175074ced2a6849d72531613b320492698e5fe935d10a'


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


stars = module('starsl_promql', 'roles/platform/grafana_starsl_dashboard/files/render_dashboard.py')
host = module('host_promql', 'roles/platform/grafana_host_dashboard_scope/files/render_dashboards.py')


@unittest.skipUnless(os.environ.get('OBS_DASHBOARD_PROMQL_TESTS') == '1', 'Explicit disposable Docker query-engine test')
class DashboardPromQL(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.name = 'dashboard-promql-' + str(os.getpid())
        subprocess.run(['docker', 'run', '--rm', '-d', '--name', cls.name, '--user', '65534:65534',
            '--read-only', '--tmpfs', '/tmp:rw,nosuid,noexec,size=128m',
            '-p', '127.0.0.1::8428', IMAGE, '-storageDataPath=/tmp/data', '-search.latencyOffset=0s'],
            check=True, capture_output=True, timeout=60)
        cls.addClassCleanup(lambda: subprocess.run(['docker', 'rm', '-f', cls.name], capture_output=True, timeout=20))
        port = subprocess.check_output(['docker', 'port', cls.name, '8428/tcp'], text=True, timeout=10).strip().rsplit(':', 1)[1]
        cls.url = 'http://127.0.0.1:' + port
        for _ in range(50):
            try:
                with urllib.request.urlopen(cls.url + '/health', timeout=1):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError('Disposable VictoriaMetrics did not become healthy')
        cls.at = int(time.time()) - 60
        samples = []
        common = 'environment="uat",job="kube-state-metrics"'
        for cluster in ['untainted', 'multiple', 'empty']:
            if cluster != 'empty':
                for node in ['node1', 'node2']:
                    samples.append('kube_node_info{cluster="' + cluster + '",node="' + node + '",' + common + '} 1 ' + str(cls.at * 1000))
            if cluster == 'multiple':
                for key in ['dedicated', 'team']:
                    samples.append('kube_node_spec_taint{cluster="multiple",node="node1",key="' + key + '",' + common + '} 1 ' + str(cls.at * 1000))
        labels = 'cluster="recent",environment="uat",job="node-exporter",instance="node1"'
        # Only twelve minutes of history: a five-minute subquery has recent
        # evaluation points, without implying a full seven days of data.
        for step in range(13):
            stamp = cls.at - (12 - step) * 60
            samples.extend([
                'node_cpu_seconds_total{' + labels + ',cpu="0",mode="idle"} ' + str(100 + step * 45) + ' ' + str(stamp * 1000),
                'node_memory_MemAvailable_bytes{' + labels + '} 100 ' + str(stamp * 1000),
                'node_memory_MemTotal_bytes{' + labels + '} 1000 ' + str(stamp * 1000)])
        for name, phase, value in [('running', 'Running', 1), ('pending', 'Pending', 2),
                                   ('unknown', 'Unknown', 3), ('done', 'Succeeded', 10), ('failed', 'Failed', 20)]:
            labels = ('cluster="lifecycle",environment="uat",job="kube-state-metrics",namespace="apps",'
                      'pod="' + name + '",uid="' + name + '"')
            samples.append('kube_pod_info{' + labels + '} 1 ' + str(cls.at * 1000))
            samples.append('kube_pod_status_phase{' + labels + ',phase="' + phase + '"} 1 ' + str(cls.at * 1000))
            if phase in ['Succeeded', 'Failed']:
                samples.append('kube_pod_status_phase{' + labels + ',phase="Running"} 0 ' + str(cls.at * 1000))
            for kind in ['requests', 'limits']:
                samples.append('kube_pod_container_resource_' + kind + '{' + labels
                               + ',resource="cpu",unit="core"} ' + str(value) + ' ' + str(cls.at * 1000))
        # Reused name with an old UID must not join the new Running Pod.
        samples.append('kube_pod_container_resource_requests{cluster="lifecycle",environment="uat",job="kube-state-metrics",namespace="apps",pod="running",uid="retired",resource="cpu",unit="core"} 100 ' + str(cls.at * 1000))
        request = urllib.request.Request(cls.url + '/api/v1/import/prometheus',
            data=('\n'.join(samples) + '\n').encode(), method='POST')
        with urllib.request.urlopen(request, timeout=5) as response:
            if response.status != 204:
                raise RuntimeError('Fixture import failed')
        # The import API acknowledges before every series is query-visible.
        # Require the complete fixture, not the first node sample to appear.
        checks = [
            'count(kube_node_info)==4',
            'count(kube_pod_info{cluster="lifecycle"})==5',
            'count(kube_pod_status_phase{cluster="lifecycle"})==7',
            'count(kube_pod_container_resource_requests{cluster="lifecycle"})==6',
            'count(kube_pod_container_resource_limits{cluster="lifecycle"})==5',
            'sum(count_over_time(node_cpu_seconds_total{cluster="recent"}[15m]))==13',
            'sum(count_over_time(node_memory_MemAvailable_bytes{cluster="recent"}[15m]))==13',
            'sum(count_over_time(node_memory_MemTotal_bytes{cluster="recent"}[15m]))==13']
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            params = urllib.parse.urlencode({'query': ' and '.join('(' + q + ')' for q in checks),
                                             'time': str(cls.at), 'nocache': '1'})
            with urllib.request.urlopen(cls.url + '/api/v1/query?' + params, timeout=5) as response:
                rows = json.load(response)['data']['result']
            if rows:
                break
            time.sleep(0.1)
        else:
            raise RuntimeError('Complete fixture samples not visible within bounded wait')

    def query(self, expr, cluster):
        expr = expr.replace('${cluster:regex}', cluster).replace('${environment:regex}', 'uat')
        params = urllib.parse.urlencode({'query': expr, 'time': str(self.at), 'nocache': '1'})
        with urllib.request.urlopen(self.url + '/api/v1/query?' + params, timeout=10) as response:
            payload = json.load(response)
        self.assertEqual(payload['status'], 'success')
        return payload['data']['result']

    def test_untainted_inventory_is_not_lost_when_taint_series_absent(self):
        rows = self.query(stars.untainted_node_count(), 'untainted')
        self.assertEqual([float(row['value'][1]) for row in rows], [2])

    def test_multiple_taints_count_each_node_once(self):
        rows = self.query(stars.untainted_node_count(), 'multiple')
        self.assertEqual([float(row['value'][1]) for row in rows], [1])

    def test_no_node_inventory_does_not_generate_zero(self):
        self.assertEqual(self.query(stars.untainted_node_count(), 'empty'), [])

    def test_raw_p99_has_percent_values_and_instance_with_recent_history(self):
        for ref, expected in [('B', 25), ('C', 90)]:
            rows = self.query(host.node_p99_queries()[ref], 'recent')
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['metric']['instance'], 'node1')
            self.assertAlmostEqual(float(rows[0]['value'][1]), expected, places=5)


    def test_resource_totals_exclude_terminal_pods_and_reused_uids(self):
        for kind in ['requests', 'limits']:
            expr = stars.active_pod_selector('kube_pod_container_resource_' + kind,
                    'cluster=~"${cluster:regex}",environment=~"${environment:regex}",resource="cpu",unit="core"')
            rows = self.query('sum(' + expr + ')', 'lifecycle')
            self.assertEqual(len(rows), 1)
            self.assertEqual(float(rows[0]['value'][1]), 6)

    def test_pod_counts_use_the_same_nonterminal_population(self):
        expr = stars.active_pod_selector('kube_pod_info',
                'cluster=~"${cluster:regex}",environment=~"${environment:regex}"')
        rows = self.query('count(' + expr + ')', 'lifecycle')
        self.assertEqual(float(rows[0]['value'][1]), 3)

    def test_missing_pod_phase_is_not_fabricated_as_zero(self):
        expr = stars.active_pod_selector('kube_pod_container_resource_requests',
                'cluster=~"${cluster:regex}",environment=~"${environment:regex}",resource="cpu"')
        self.assertEqual(self.query('sum(' + expr + ')', 'empty'), [])


if __name__ == '__main__':
    unittest.main()
