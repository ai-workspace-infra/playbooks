"""Verify OS key retrieval against a fake HTTPS Vault endpoint, without host changes."""
import json
import os
from pathlib import Path
import ssl
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]


class Reader(unittest.TestCase):
    def test_role_reads_three_keys_and_rejects_missing_record(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(root/'key.pem'), '-out', str(root/'cert.pem'), '-days', '1', '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(root/'ssh')], check=True)
            pubkey = (root/'ssh.pub').read_text().strip()
            state = {'missing': False, 'paths': []}

            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    state['paths'].append(self.path)
                    if self.headers.get('X-Vault-Token') != 'test-reader' or (state['missing'] and self.path.endswith('/auditor')):
                        self.send_response(403)
                        self.end_headers()
                        return
                    payload = json.dumps({'data': {'data': {'authorized_keys': pubkey}}}).encode()
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(payload)
                def log_message(self, *_args):
                    pass

            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(root/'cert.pem', root/'key.pem')
            server.socket = context.wrap_socket(server.socket, server_side=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            tasks = yaml.safe_load((ROOT/'roles/vhosts/common/tasks/os_identity.yml').read_text())[:5]
            play = root/'play.yml'
            play.write_text(yaml.safe_dump([{'hosts': 'localhost', 'gather_facts': False, 'vars': {'ansible_os_family': 'Debian', 'common_os_identity_environment': 'prod'}, 'tasks': tasks}]))
            env = dict(os.environ, VAULT_ADDR=f'https://localhost:{server.server_port}', VAULT_TOKEN='test-reader', SSL_CERT_FILE=str(root/'cert.pem'), NO_PROXY='localhost,127.0.0.1')
            def run():
                return subprocess.run(['ansible-playbook', '-i', 'localhost,', '-c', 'local', str(play)], cwd=ROOT, env=env, capture_output=True, text=True, timeout=40)
            result = run()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(set(state['paths']), {f'/v1/kv/data/prod/security/{role}' for role in ('root', 'operator', 'auditor')})
            state['missing'] = True
            self.assertNotEqual(run().returncode, 0)


if __name__ == '__main__':
    unittest.main()
