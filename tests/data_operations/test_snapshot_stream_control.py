"""No database access: timeout and redacted failure-code process contracts."""
import os,pathlib,subprocess,sys,threading,unittest
from unittest.mock import patch
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[2]/'scripts/data_operations'))
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[2]/'scripts/data_operations/selfhost'))
import export_prod_business_snapshot as exporter
from full_business_contract import BUSINESS_TABLES,LEGACY_BUSINESS_TABLES

class SnapshotStreamControl(unittest.TestCase):
    def exercise(self,source_program,timer=None):
        real_popen=subprocess.Popen
        def popen(argv,**kwargs):
            if argv[0]=='psql':argv=[sys.executable,'-c',source_program]
            return real_popen(argv,**kwargs)
        destination=[sys.executable,'-c','import sys;sys.stdin.buffer.read()']
        tables=tuple(t for t in BUSINESS_TABLES if t in LEGACY_BUSINESS_TABLES)
        with patch.object(exporter,'connection_env',return_value=dict(os.environ)),patch.object(exporter.subprocess,'Popen',side_effect=popen):
            if timer:
                with patch.object(exporter.threading,'Timer',side_effect=timer):
                    return exporter.stream('no-database',destination,'nonprivate-fixture-key',tables)
            return exporter.stream('no-database',destination,'nonprivate-fixture-key',tables)

    def test_timeout_is_distinct_and_kills_both_children(self):
        real_timer=threading.Timer;budgets=[]
        def expire_soon(seconds,callback):
            budgets.append(seconds)
            return real_timer(0.1,callback)
        with self.assertRaises(exporter.SnapshotFailure) as caught:
            self.exercise('import time;time.sleep(10)',expire_soon)
        self.assertEqual(caught.exception.code,'overall_timeout_1800s')
        self.assertEqual(budgets,[1800])

    def test_source_sqlstate_does_not_include_private_message(self):
        with self.assertRaises(exporter.SnapshotFailure) as caught:
            self.exercise("import sys;sys.stderr.write('ERROR: 57014 private-fixture-do-not-log\\n');sys.exit(1)")
        self.assertEqual(str(caught.exception),'source_sql_57014')
        self.assertNotIn('private-fixture',str(caught.exception))

if __name__=='__main__':unittest.main()
