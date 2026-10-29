import copy
import hashlib
import json
import unittest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import dev_feature_baseline_inputs as baseline

class BaselineInputsTest(unittest.TestCase):
    def setUp(self):
        self.sql = b'CREATE TABLE public.example(id bigint);\n'
        self.billing = b'CREATE TABLE public.billing(id bigint);\n'
        self.manifest = {'format': 1, 'migration_version': 1, 'schema_sha256': hashlib.sha256(self.sql).hexdigest(),
                         'business_table_count': 1, 'business_tables': ['example']}
        self.refs = {'accounts_sha': 'a'*40, 'billing_sha': 'b'*40,
                     'accounts_image': 'ghcr.io/ai-workspace-services/accounts@sha256:' + 'c'*64}
    def run_check(self, source=None, image=None, sql=None, refs=None):
        source = self.manifest if source is None else source
        image = source if image is None else image
        return baseline.inspect(json.dumps(source).encode(), self.sql if sql is None else sql, self.billing,
                                json.dumps(image).encode(), **(self.refs if refs is None else refs))
    def test_consistent_inputs_have_deterministic_digest_but_no_runtime_claim(self):
        result = self.run_check()
        self.assertEqual(result, self.run_check())
        self.assertEqual(result['evidence_status'], 'INPUTS_ONLY')
        self.assertEqual(result['database_status'], 'NOT_RUN')
    def test_rejects_changed_image_sql_and_invalid_refs(self):
        changed = copy.deepcopy(self.manifest); changed['migration_version'] += 1
        for kwargs in ({'image': changed}, {'sql': b'changed'}, {'refs': dict(self.refs, accounts_sha='main')}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError): self.run_check(**kwargs)
    def test_rejects_duplicate_or_invalid_tables(self):
        for tables in (['example','example'], ['bad.name'], []):
            source = dict(self.manifest, business_tables=tables, business_table_count=len(tables))
            with self.subTest(tables=tables), self.assertRaises(ValueError): self.run_check(source=source)

if __name__ == '__main__': unittest.main()
