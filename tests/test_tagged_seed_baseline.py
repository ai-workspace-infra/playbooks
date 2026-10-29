import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from tagged_seed_baseline import validate, validate_receipt_inputs


class TaggedSeedBaselineTest(unittest.TestCase):
    def setUp(self):
        self.uid = '12345678-1234-1234-1234-123456789abc'
        self.inputs = {'schema': 'sit-data-input/v1', 'accounts_image': 'ghcr.io/ai-workspace-services/accounts@sha256:'+'a'*64,
                       'accounts_sha': 'b'*40, 'billing_sha': 'c'*40, 'accounts_manifest_sha256': '1'*64,
                       'accounts_native_sha256': '2'*64, 'billing_schema_sha256': '3'*64,
                       'image_manifest_sha256': '4'*64, 'migration_version': 1, 'business_table_count': 1}
        digest = hashlib.sha256(json.dumps(self.inputs, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        lane = {'feature_id': 'sit-test-1483', 'feature_tag': 'smoke-sit-test-1483', 'candidate_sha256': 'd'*64}
        self.receipt = {'schema_version': 'dev-feature-candidate/v2', 'result': 'verified', 'stage': 'sit',
                        'candidate_sha256': 'd'*64, 'data_baseline_sha256': digest,
                        'projection': {'tagged_lane': lane, 'components': {
                            'accounts': {'source_repository': 'ai-workspace-services/accounts', 'image': self.inputs['accounts_image'], 'source_sha': self.inputs['accounts_sha']},
                            'billing': {'source_repository': 'ai-workspace-services/billing-service', 'source_sha': self.inputs['billing_sha']}}}}
        self.baseline = {'schema': 'sit-data-baseline/v1', 'result': 'verified', 'database_status': 'NOT_RUN',
                         'feature_id': lane['feature_id'], 'namespace': 'feature-sit-test-1483-sit',
                         'data_baseline_sha256': digest, 'inputs': self.inputs}

    def test_matching_candidate_and_baseline(self):
        self.assertEqual(validate(self.receipt, self.baseline, self.uid)['namespace_uid'], self.uid)
        self.receipt['data_baseline_inputs'] = self.inputs
        self.assertEqual(validate_receipt_inputs(self.receipt, self.uid)['data_baseline_sha256'], self.baseline['data_baseline_sha256'])
        self.receipt['data_baseline_inputs']['billing_sha'] = 'e'*40
        with self.assertRaises(ValueError): validate_receipt_inputs(self.receipt, self.uid)

    def test_rejects_cross_lane_stale_or_changed_inputs(self):
        for kind in ('namespace', 'source', 'digest', 'tag', 'uid', 'repository', 'count'):
            receipt, baseline, uid = copy.deepcopy(self.receipt), copy.deepcopy(self.baseline), self.uid
            if kind == 'namespace': baseline['namespace'] = 'feature-other-sit'
            if kind == 'source': receipt['projection']['components']['billing']['source_sha'] = 'e'*40
            if kind == 'digest': baseline['inputs']['billing_sha'] = 'e'*40
            if kind == 'tag': receipt['projection']['tagged_lane']['feature_tag'] = 'other-sit-test-1484'
            if kind == 'uid': uid = 'invalid'
            if kind == 'repository': receipt['projection']['components']['accounts']['source_repository'] = 'other/accounts'
            if kind == 'count': baseline['inputs']['business_table_count'] = True
            with self.subTest(kind=kind), self.assertRaises(ValueError): validate(receipt, baseline, uid)


if __name__ == '__main__':
    unittest.main()
