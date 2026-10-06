import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('runtime', ROOT / 'scripts/data_operations/run_import_with_receipt.py')
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


class ImportReceiptTests(unittest.TestCase):
    def test_owner_job_context_is_only_used_at_allowed_step_scope(self):
        workflow = yaml.safe_load((ROOT / '.github/workflows/uat-data-import.yaml').read_text())
        job = workflow['jobs']['import']
        for value in job.get('env', {}).values():
            self.assertNotIn('job.', str(value), 'job context is unavailable in jobs.<job_id>.env')
        receipt = next(step for step in job['steps'] if step['name'] == 'Write sanitized execution receipt')
        self.assertEqual(receipt['env']['IMPORT_OWNER_SHA'], '${{ job.workflow_sha }}')

    def summary(self, stdout, stderr='', rc=0, dry_run=False, timed_out=False):
        return runtime.summarize(stdout, stderr, rc, 'accounts_data_migration_target_tunnel.sh', dry_run, timed_out)

    def test_schema_failure_before_writes(self):
        r = self.summary('[STEP 1/4]\n[STEP 2/4]', 'column "uuid" does not exist (SQLSTATE 42703)', 1)
        self.assertEqual((r['phase'], r['category'], r['write_state'], r['sqlstate']),
                         ('target_preview', 'database_schema', 'not_attempted', '42703'))

    def test_preview_does_not_claim_applied_import(self):
        r = self.summary('[STEP 2/4]\n[STEP 3/4] DRY_RUN=true\n[STEP 4/4] Skipping', dry_run=True)
        self.assertEqual(r['write_state'], 'not_attempted')
        self.assertFalse(r['convergence_verified'])

    def test_partial_apply_and_failed_verify_remain_unverified(self):
        for output in ('[STEP 3/4]', '[STEP 3/4]\n[STEP 4/4] Verifying convergence'):
            r = self.summary(output, rc=1)
            self.assertEqual(r['write_state'], 'unverified')
            self.assertFalse(r['convergence_verified'])

    def test_success_requires_convergence_marker(self):
        self.assertEqual(self.summary('[STEP 3/4]')['write_state'], 'unverified')
        r = self.summary('[STEP 3/4]\n[STEP 4/4] Verifying convergence\n[STEP 4/4] Verification passed:')
        self.assertEqual((r['phase'], r['write_state']), ('target_verify', 'verified'))
        self.assertTrue(r['convergence_verified'])

    def test_timeout_after_apply_is_not_a_rollback_receipt(self):
        r = self.summary('[STEP 3/4]', rc=-15, timed_out=True)
        self.assertEqual((r['category'], r['write_state']), ('execution_timeout', 'unverified'))

    def test_public_receipt_only_copies_whitelisted_runtime_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'uat-import-runtime.json').write_text(json.dumps(dict(
                self.summary('[STEP 2/4]', rc=1), raw='synthetic-secret')))
            env = dict(os.environ, RUNNER_TEMP=directory, CONFIG_JSON='{"accounts_target_host":"web-saas-uat","caller_run_id":"42"}',
                       CORRELATION_ID='test', REQUESTED_ENVIRONMENT='uat', GITHUB_RUN_ID='123', GITHUB_RUN_ATTEMPT='1',
                       ACCOUNTS_REF='a'*40, ACCOUNTS_SOURCE_SHA='a'*40, IMPORT_OWNER_SHA='b'*40)
            result = subprocess.run(['python3', str(ROOT / 'roles/uat_data_import/files/write_receipt.py')],
                                    env=env, cwd=directory, text=True, capture_output=True, check=True)
            self.assertNotIn('synthetic-secret', result.stdout)
            receipt = json.loads(result.stdout)
            self.assertEqual(receipt['accounts_sha'], 'a'*40)
            self.assertEqual(receipt['target_host'], 'web-saas-uat')
            self.assertEqual(receipt['runtime']['write_state'], 'not_attempted')


if __name__ == '__main__':
    unittest.main()
