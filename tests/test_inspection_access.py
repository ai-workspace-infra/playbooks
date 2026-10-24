import io
import runpy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jinja2 import Environment, StrictUndefined

ROOT = Path(__file__).resolve().parents[1]


class InspectionGatewayTest(unittest.TestCase):
    def invoke(self, args, data=''):
        template = (ROOT / 'roles/inspection_access/templates/platform-inspect.py.j2').read_text()
        source = Environment(undefined=StrictUndefined).from_string(template).render(
            inspection_container='test-postgres', inspection_db_role='db_readonly', inspection_database='testdb')
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py') as f:
            f.write(source)
            f.flush()
            with patch('sys.argv', ['gateway'] + args), patch('sys.stdin', io.StringIO(data)), \
                    patch('subprocess.run') as run, patch.dict('os.environ', {}, clear=True):
                run.return_value.returncode = 0
                with self.assertRaises(SystemExit):
                    runpy.run_path(f.name, run_name='__main__')
                return run.call_args

    def test_forbidden_operations_never_execute(self):
        for args, data in [(['exec'], ''), (['containers', '--extra'], ''),
                           (['container-logs'], '--privileged'), (['container-logs'], 'foo;id')]:
            with self.subTest(args=args, data=data):
                self.assertIsNone(self.invoke(args, data))

    def test_sql_uses_reader_and_never_psql_meta_command_mode(self):
        call = self.invoke(['database'], '\\! touch /tmp/not-allowed')
        argv = call.args[0]
        self.assertEqual(argv[argv.index('-U') + 1], 'db_readonly')
        self.assertTrue(argv[-1].startswith('BEGIN READ ONLY;'))
        self.assertNotIn('shell', call.kwargs)

    def test_logs_are_bounded_and_have_no_pager(self):
        argv = self.invoke(['logs']).args[0]
        self.assertIn('--no-pager', argv)
        self.assertEqual(argv[-2:], ['-n', '200'])
        argv = self.invoke(['container-logs'], 'test-container').args[0]
        self.assertEqual(argv[-4:], ['--tail', '200', '--', 'test-container'])


if __name__ == '__main__':
    unittest.main()
