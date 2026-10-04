"""Prove artifact provenance is checked before host credentials are accessed."""
import io
import json
import os
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/data_operations/selfhost/verify_caller_cmdb.py'


class CallerProvenanceTests(unittest.TestCase):
    def execute(self, **changes):
        run = dict(repository={'full_name': 'ai-workspace-infra/platform-ops-toolkit'},
                   workflow_id=42, head_sha='a' * 40, head_branch='main', event='workflow_dispatch',
                   status='in_progress', conclusion=None)
        run.update(changes)
        responses = [io.BytesIO(json.dumps(run).encode()),
                     io.BytesIO(b'{"path":".github/workflows/selfhost-orchestrator.yml"}')]
        with tempfile.TemporaryDirectory() as directory:
            cmdb = Path(directory) / 'cmdb.json'
            cmdb.write_text(json.dumps({'environment': 'uat', 'web-saas-uat':
                                       {'ip': '192.0.2.1', 'groups': ['web_saas']}}))
            env = dict(REQUESTED_ENVIRONMENT='uat', CONFIG_JSON='{"target_host":"web-saas-uat"}',
                       CALLER_RUN_ID='123', GH_TOKEN='offline-test', CMDB_FILE=str(cmdb))
            with patch.dict(os.environ, env, clear=True), patch('urllib.request.urlopen', side_effect=responses):
                runpy.run_path(str(SCRIPT), run_name='__main__')

    def test_active_main_dispatch(self):
        self.execute()

    def test_successful_reviewed_tag(self):
        self.execute(head_branch='uat-daily-build-2026.10.04-r1', status='completed', conclusion='success')

    def test_untrusted_refs_and_events(self):
        for changes in ({'event': 'pull_request'}, {'head_branch': 'feature/unreviewed'}, {'head_branch': 'unset'}):
            with self.subTest(changes=changes), self.assertRaises(SystemExit):
                self.execute(**changes)

    def test_failed_cancelled_or_queued_runs(self):
        for changes in ({'status': 'completed', 'conclusion': 'failure'},
                        {'status': 'completed', 'conclusion': 'cancelled'}, {'status': 'queued'}):
            with self.subTest(changes=changes), self.assertRaises(SystemExit):
                self.execute(**changes)


if __name__ == '__main__':
    unittest.main()
