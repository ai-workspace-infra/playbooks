"""become_from_vault.yml reads the SSH user's become password from a fake HTTPS Vault."""
import json
import os
from pathlib import Path
import ssl
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest

ROOT = Path(__file__).resolve().parents[1]
PLAY = """
- hosts: localhost
  gather_facts: false
  vars: {{ansible_user: {user}, common_become_environment: sit}}
  tasks:
    - ansible.builtin.include_role: {{name: vhosts/common, tasks_from: become_from_vault.yml}}
    - ansible.builtin.copy:
        dest: "{out}"
        content: "{{{{ ansible_become_password | default('unset') }}}}"
"""


class BecomeFromVault(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        root = Path(self.dir.name)
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(root/'key.pem'), '-out', str(root/'cert.pem'), '-days', '1', '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.paths = []
        paths = self.paths

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                paths.append(self.path)
                if self.headers.get('X-Vault-Token') != 'test' or not self.path.endswith('/security/become/operator'):
                    self.send_response(403)
                    self.end_headers()
                    return
                body = json.dumps({'data': {'data': {'password': 'from-vault'}}}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(root/'cert.pem', root/'key.pem')
        server.socket = context.wrap_socket(server.socket, server_side=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.env = dict(os.environ, VAULT_ADDR=f'https://localhost:{server.server_port}', VAULT_TOKEN='test', SSL_CERT_FILE=str(root/'cert.pem'), NO_PROXY='localhost,127.0.0.1', ANSIBLE_ROLES_PATH=str(ROOT/'roles'))

    def run_as(self, user):
        root = Path(self.dir.name)
        out = root/f'{user}.out'
        play = root/f'{user}.yml'
        play.write_text(PLAY.format(user=user, out=out))
        result = subprocess.run(['ansible-playbook', '-i', 'localhost,', '-c', 'local', str(play)], cwd=ROOT, env=self.env, capture_output=True, text=True, timeout=60)
        return result, out

    def test_operator_password_is_bound_from_its_own_record(self):
        result, out = self.run_as('operator')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(out.read_text(), 'from-vault')
        self.assertEqual(self.paths, ['/v1/kv/data/sit/security/become/operator'])
        self.assertNotIn('from-vault', result.stdout + result.stderr)

    def test_root_skips_vault(self):
        result, out = self.run_as('root')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(out.read_text(), 'unset')
        self.assertEqual(self.paths, [])

    def test_other_user_without_record_fails(self):
        result, _ = self.run_as('auditor')
        self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
