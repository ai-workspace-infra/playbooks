import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]

class HandoverBoundaryTest(unittest.TestCase):
    def test_bootstrap_does_not_wait_for_workload_readiness(self):
        tasks = (ROOT / 'roles/vhosts/k3s_platform_bootstrap/tasks/main.yml').read_text()
        entry = (ROOT / 'scripts/pipeline/bootstrap-k3s.sh').read_text()
        action = (ROOT / '.github/actions/k3s-bootstrap/action.yml').read_text()
        self.assertNotIn('vhosts/flux_workload_readiness', tasks)
        self.assertNotIn('FLUX_OBJECTS_READY', entry)
        self.assertNotIn('readiness-receipt:', action)
        self.assertIn('FLUX_HANDOVER_VERIFIED', entry)
        self.assertIn('K3S_IDENTITIES_VERIFIED', entry)

if __name__ == '__main__':
    unittest.main()
