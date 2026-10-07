import json
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
VERIFIER = ROOT / 'roles/vhosts/xconnect_lab_runtime/files/verify_xhttp_runtime.py'


class XConnectXhttpRuntimeVerifierTests(unittest.TestCase):
    def run_verifier(self, role, path, *, remote='tw-xconnect.svc.plus', xhttp_path='/xconnect'):
        return subprocess.run([
            'python3', str(VERIFIER), '--role', role, '--config-path', str(path),
            '--remote-address', remote, '--server-name', 'tw-xconnect.svc.plus',
            '--xhttp-path', xhttp_path, '--xhttp-mode', 'auto',
            '--xhttp-host', 'tw-xconnect.svc.plus',
        ], capture_output=True, text=True)

    def test_gateway_direct_tls_and_caddy_unix_contracts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for mode, listen, port, security in (
                ('direct-tls', '0.0.0.0', 443, 'tls'),
                ('caddy-unix', '/run/xconnect-gateway/xray.sock,0660', None, None),
            ):
                config = root / f'{mode}.json'
                stream = {'network': 'xhttp', 'xhttpSettings': {
                    'path': '/xconnect', 'mode': 'auto', 'host': 'tw-xconnect.svc.plus'}}
                if security:
                    stream.update(security='tls', tlsSettings={'rejectUnknownSni': True})
                config.write_text(json.dumps({
                    'inbounds': [{'listen': listen, 'port': port, 'protocol': 'vless', 'streamSettings': stream}],
                    'outbounds': [{'tag': 'xconnect-wireguard', 'protocol': 'freedom',
                                   'settings': {'redirect': '127.0.0.1:51820'}}],
                }))
                result = self.run_verifier('gateway', config)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(f'mode={mode}', result.stdout)
                rejected = self.run_verifier('gateway', config, xhttp_path='/wrong')
                self.assertNotEqual(rejected.returncode, 0)

    def test_one_uses_only_the_active_absolute_config(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'state'
            revision = state / 'runtime/revisions/abc'
            revision.mkdir(parents=True)
            config = revision / 'xray.json'
            config.write_text(json.dumps({
                'inbounds': [{'listen': '127.0.0.1', 'port': 51830, 'protocol': 'dokodemo-door',
                              'settings': {'network': 'udp'}}],
                'outbounds': [{'protocol': 'vless', 'settings': {'vnext': [
                    {'address': 'tw-xconnect.svc.plus', 'port': 443}]},
                    'streamSettings': {'network': 'xhttp', 'security': 'tls',
                        'tlsSettings': {'serverName': 'tw-xconnect.svc.plus'},
                        'xhttpSettings': {'path': '/xconnect', 'mode': 'auto',
                                          'host': 'tw-xconnect.svc.plus'}}}],
            }))
            (state / 'runtime/active.json').write_text(json.dumps({'xray_config_path': str(config)}))
            result = self.run_verifier('one', state)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotEqual(self.run_verifier('one', state, remote='wrong.example').returncode, 0)


if __name__ == '__main__':
    unittest.main()
