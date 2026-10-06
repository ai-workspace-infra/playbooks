import json,pathlib,sys,unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[2]/'scripts/data_operations'))
from full_business_contract import LEGACY_BUSINESS_TABLES,BUSINESS_TABLES
from full_business_snapshot import SnapshotValidator,snapshot_sql

TABLES=tuple(t for t in BUSINESS_TABLES if t in LEGACY_BUSINESS_TABLES)
class SnapshotContract(unittest.TestCase):
    def header(self):return json.dumps({'kind':'header','schema':'full-business-snapshot/v1','tables':list(TABLES),'columns':{t:[{'name':'uuid','type':'uuid'}] for t in TABLES}})
    def test_all_reviewed_tables_and_consistent_readonly_transaction(self):
        sql=snapshot_sql(TABLES)
        self.assertIn('REPEATABLE READ READ ONLY',sql)
        for t in TABLES:self.assertIn('FROM public."'+t+'"',sql)
        self.assertNotIn('schema_migrations',sql);self.assertNotIn('INSERT',sql)
        with self.assertRaises(ValueError):snapshot_sql(TABLES+('unknown_business',))
    def test_complete_stream_counts(self):
        v=SnapshotValidator(TABLES);v.accept(self.header());v.accept(json.dumps({'kind':'row','table':'users','row':{'uuid':'private-fixture'}}))
        counts={t:int(t=='users') for t in TABLES};v.accept(json.dumps({'kind':'footer','counts':counts}));self.assertEqual(v.finish(),counts)
    def test_truncated_mismatch_unknown_and_wrong_order(self):
        v=SnapshotValidator(TABLES)
        with self.assertRaises(ValueError):v.finish()
        with self.assertRaises(ValueError):v.accept(json.dumps({'kind':'row','table':'users','row':{}}))
        v.accept(self.header())
        with self.assertRaises(ValueError):v.accept(self.header())
        with self.assertRaises(ValueError):v.accept(json.dumps({'kind':'row','table':'schema_migrations','row':{}}))
        with self.assertRaises(ValueError):v.accept(json.dumps({'kind':'footer','counts':{'users':999}}))

if __name__=='__main__':unittest.main()
