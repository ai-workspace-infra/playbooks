import importlib.util
from pathlib import Path
import subprocess
import unittest


PATH = Path(__file__).resolve().parents[1] / 'roles/platform/dev_feature_flux/files/reconcile.py'
SPEC = importlib.util.spec_from_file_location('dev_feature_failure_codes', PATH)
OWNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(OWNER)


class OwnerFailureCodeTests(unittest.TestCase):
 def test_rebuild_failure_codes_are_bounded_and_actionable(self):
    self.assertEqual(OWNER.failure_code('rebuild', ValueError('SIT namespace incarnation changed before rebuild')), 'sit_rebuild_namespace_uid_mismatch')
    self.assertEqual(OWNER.failure_code('rebuild', ValueError('SIT rebuild cannot safely reclaim this persistent volume')), 'sit_rebuild_pv_policy_rejected')
    self.assertEqual(OWNER.failure_code('rebuild', ValueError('SIT local-path volume reclamation timed out; namespace remains deleted and rebuild is blocked')), 'sit_rebuild_pv_reclaim_timeout')


 def test_seed_failure_codes_use_same_allowlist(self):
    blocked = type('Blocked', (Exception,), {})
    self.assertEqual(OWNER.failure_code('seed-admin', blocked('namespace UID changed or stale target')), 'sit_seed_namespace_uid_mismatch')
    self.assertEqual(OWNER.failure_code('initialize', blocked('Kubernetes operation failed (sensitive output suppressed)')), 'sit_seed_kubernetes_operation_failed')


 def test_unknown_failures_never_copy_exception_text_into_receipt_code(self):
    marker = 'private-error-with-secret-like-value'
    code = OWNER.failure_code('rebuild', RuntimeError(marker))
    self.assertEqual(code, 'owner_rejected_or_internal_error')
    self.assertNotIn(marker, code)
    self.assertEqual(OWNER.failure_code('unknown', subprocess.TimeoutExpired(['owner'], 3)), 'owner_timeout')
    self.assertEqual(OWNER.failure_code('unknown', OSError(marker)), 'owner_internal_error')


if __name__ == '__main__':
 unittest.main()
