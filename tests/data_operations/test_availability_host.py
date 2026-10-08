import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('availability', ROOT / 'scripts/data_operations/selfhost/availability_host.py')
HOST = importlib.util.module_from_spec(spec)
spec.loader.exec_module(HOST)


class AvailabilityTests(unittest.TestCase):
    def test_sync_requires_fresh_poll_no_error_and_no_active_deployment(self):
        before = dict(doco_cd_polls_total=1, doco_cd_deployments_total=1)
        after = {**before, 'doco_cd_polls_total': 2}
        self.assertTrue(HOST.synchronized(before, after))
        self.assertFalse(HOST.synchronized(before, before))
        for key in ('doco_cd_poll_errors_total','doco_cd_deployment_errors_total','doco_cd_deployments_active','doco_cd_deployments_queued'):
            self.assertFalse(HOST.synchronized(before, {**after, key: 1}))

    def test_missing_metrics_cannot_pass(self):
        with self.assertRaises(RuntimeError):
            HOST.metrics('# no samples')

    def execute(self, failure=None):
        calls = []
        metric_count = 0
        def command(argv):
            nonlocal metric_count
            calls.append(argv)
            if argv[:2] == ['docker','inspect']:
                if failure == 'containers':
                    return 'false:unhealthy'
                if 'IPAddress' in argv[3]:
                    return '172.18.0.5'
                return 'true:healthy'
            if argv[:2] == ['docker','exec']:
                return '0' if failure == 'database' else '1'
            if 'http://127.0.0.1:9120/metrics' in argv:
                metric_count += 1
                return ('doco_cd_polls_total{repository="https://github.com/ai-workspace-infra/gitops.git"} '+str(metric_count)+'\n'
                    'doco_cd_deployments_total{repository="https://github.com/ai-workspace-infra/gitops.git",deployment="web-saas"} 1')
            return '503' if failure in ('https','api') and (failure != 'api' or 'http://172.18.0.5' in argv[-1]) else '200'
        output = io.StringIO()
        with patch.object(HOST,'command',side_effect=command), patch('sys.stdout',output):
            if failure:
                with self.assertRaises(SystemExit): HOST.main()
            else:
                HOST.main()
        return calls,json.loads(output.getvalue())

    def test_availability_verifies_tls_and_only_reads_database(self):
        calls, receipt = self.execute()
        self.assertEqual(receipt['result'],'available')
        self.assertFalse(receipt['target_writes'])
        self.assertTrue(receipt['caddy_running'])
        self.assertTrue(any('BEGIN READ ONLY; SELECT 1; COMMIT;' in call for call in calls))
        tls = [call for call in calls if any(item.startswith('https://') for item in call)]
        self.assertEqual(len(tls),2)
        for call in tls:
            self.assertIn('--resolve',call)
            self.assertNotIn('-k',call)
        self.assertNotIn('stop',str(calls))

    def test_failed_probe_never_claims_available(self):
        for stage in ('containers','https','api','database'):
            _,receipt = self.execute(stage)
            self.assertEqual(receipt['result'],'failed')
            self.assertEqual(receipt['failure_stage'],stage)


if __name__ == '__main__': unittest.main()
