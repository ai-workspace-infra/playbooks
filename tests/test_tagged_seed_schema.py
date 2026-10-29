import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[1] / 'roles/platform/dev_web_saas_seed/files/contracts.py'
spec = importlib.util.spec_from_file_location('seed_contracts', path)
contracts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contracts)


class TaggedSeedSchemaTest(unittest.TestCase):
    def setUp(self):
        self.native, self.billing = b'CREATE TABLE x();', b'CREATE TABLE y();'
        tables = ['table_' + str(i) for i in range(78)]
        manifest = {'format': 1, 'migration_version': 2026102801, 'schema_sha256': hashlib.sha256(self.native).hexdigest(),
                    'business_table_count': len(tables), 'business_tables': tables}
        self.manifest = json.dumps(manifest).encode()
        self.image = self.manifest
        self.inputs = {'schema': 'sit-data-input/v1', 'migration_version': manifest['migration_version'],
                       'business_table_count': len(tables)}
        for key, raw in [('accounts_manifest_sha256', self.manifest), ('accounts_native_sha256', self.native),
                         ('billing_schema_sha256', self.billing), ('image_manifest_sha256', self.image)]:
            self.inputs[key] = hashlib.sha256(raw).hexdigest()

    def test_candidate_78_tables_validated_without_changing_legacy_default(self):
        tables = contracts.verify_tagged_sources(self.manifest, self.native, self.billing, self.image, self.inputs)
        self.assertEqual(len(tables), 78)
        self.assertIn('public.table_77', contracts.grants(tables, expected_count=78))
        with self.assertRaises(ValueError): contracts.grants(tables)

    def test_changed_bytes_or_count_fail_closed(self):
        with self.assertRaises(ValueError):
            contracts.verify_tagged_sources(self.manifest, self.native + b' ', self.billing, self.image, self.inputs)
        with self.assertRaises(ValueError):
            contracts.verify_tagged_sources(self.manifest, self.native, self.billing, self.image,
                                            dict(self.inputs, business_table_count=77))
        with self.assertRaises(ValueError): contracts.grants(['users; DROP TABLE x'], expected_count=1)

    def test_namespace_binding_requires_exact_feature_tag_and_digest(self):
        lane = {'feature_id': 'sit-test-1483', 'feature_tag': 'smoke-sit-test-1483', 'candidate_sha256': 'a'*64}
        namespace = 'feature-sit-test-1483-sit'
        contracts.target('sit', namespace, lane)
        self.assertEqual(len(contracts.identity(namespace, 'xc_accounts_rw', lane)), 64)
        for stage, ns, bad in [('sit', 'feature-other-sit', lane), ('prod', namespace, lane),
                               ('sit', namespace, dict(lane, feature_tag='smoke-sit-test-1484')),
                               ('sit', namespace, dict(lane, candidate_sha256='bad')),
                               ('sit', namespace, None)]:
            with self.subTest(stage=stage, namespace=ns, lane=bad), self.assertRaises(ValueError):
                contracts.target(stage, ns, bad)


if __name__ == '__main__':
    unittest.main()
