import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from run_dev_web_saas_seed import require_same_run_prepare


class TaggedPrepareProvenanceTest(unittest.TestCase):
    def test_only_same_protected_run_and_attempt_are_accepted(self):
        repo = 'ai-workspace-infra/Platform-Orchestrator'
        env = {'GITHUB_REPOSITORY': repo, 'GITHUB_SHA': 'a'*40, 'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '1'}
        source = {'repository': repo, 'workflow_ref': repo + '/.github/workflows/feature-lane-deploy.yml@refs/heads/main',
                  'workflow_sha': 'a'*40, 'run_id': '123', 'run_attempt': '1'}
        prior = {'run_id': '123', 'run_attempt': '1', 'registry': {'oci_mapping': {'binding': {'source': source}}}}
        require_same_run_prepare(prior, env)
        for key, value in [('run_id', '124'), ('run_attempt', '2'), ('workflow_sha', 'b'*40),
                           ('workflow_ref', repo + '/.github/workflows/other.yml@refs/heads/main')]:
            bad = copy.deepcopy(prior)
            bad['registry']['oci_mapping']['binding']['source'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): require_same_run_prepare(bad, env)


if __name__ == '__main__':
    unittest.main()
