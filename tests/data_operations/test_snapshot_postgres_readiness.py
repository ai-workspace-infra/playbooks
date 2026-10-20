"""Prove the restore gate waits past PostgreSQL's temporary init server."""
import ast
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(os.environ.get('GENERATION_READY_TEST_IMAGE'), 'requires an isolated Docker init fixture')
class SnapshotReadinessTests(unittest.TestCase):
    def test_temporary_socket_server_is_not_restore_ready(self):
        source = ROOT / 'roles/web_saas_full_snapshot/files/capture.py'
        node = next(n for n in ast.parse(source.read_text()).body
                    if isinstance(n, ast.FunctionDef) and n.name == 'wait_for_isolated_postgres')
        namespace = {'subprocess': subprocess}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), namespace)
        name = 'generation-ready-fixture-' + uuid.uuid4().hex[:10]
        with tempfile.TemporaryDirectory(prefix='pg-init-') as directory:
            script = Path(directory) / '00-wait.sh'
            script.write_text('sleep 5\n')
            subprocess.run(['docker', 'run', '-d', '--name', name, '--network', 'none',
                            '-e', 'POSTGRES_HOST_AUTH_METHOD=trust',
                            '--mount', 'type=bind,src=' + str(script) + ',dst=/docker-entrypoint-initdb.d/00-wait.sh,readonly',
                            os.environ['GENERATION_READY_TEST_IMAGE']], check=True, capture_output=True)
            try:
                for _ in range(150):
                    socket = subprocess.run(['docker', 'exec', name, 'pg_isready', '-U', 'postgres'], capture_output=True)
                    if socket.returncode == 0:
                        break
                    time.sleep(0.1)
                self.assertEqual(socket.returncode, 0)
                tcp = subprocess.run(['docker', 'exec', name, 'pg_isready', '-h', '127.0.0.1', '-U', 'postgres'], capture_output=True)
                self.assertNotEqual(tcp.returncode, 0, 'fixture did not expose the temporary-server window')
                namespace['wait_for_isolated_postgres'](name)
                result = subprocess.run(['docker', 'exec', name, 'createdb', '-U', 'postgres', 'restored'], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            finally:
                subprocess.run(['docker', 'rm', '-f', name], check=True, capture_output=True)


if __name__ == '__main__':
    unittest.main()
