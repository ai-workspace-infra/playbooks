"""Behavioural tests for the pull-mode Vector credential bootstrap helper.

ansible-pull runs with no environment, so the control-plane supplied
VECTOR_AUTH_USER/VECTOR_AUTH_PASSWORD are unavailable. The helper copies the
already-delivered credential out of the live Vector configuration into a
persistent EnvironmentFile. It must never invent, rotate or leak one.
"""

import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'roles/vhosts/vector-agent/files/bootstrap-remote-write-auth'

STATIC_CONFIG = '''
[api]
enabled = false

[sinks.billing_snapshot_ingest.request]
headers.Authorization = "Bearer SECRET[local_secret.billing_token]"

[sinks.prometheus_remote.auth]
strategy = "basic"
user = "observability"
password = "Bearer sekrit+value=/with/slash"

[sinks.prometheus_remote.tls]
verify_certificate = true
'''


def run(config_text, target, *, extra_env=None):
    """Run the helper in an isolated temp dir; return (rc, stderr)."""
    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp) / 'vector.toml'
        config.write_text(config_text)
        env = {
            'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
            'VECTOR_AUTH_STATIC_FILE': str(target),
            'VECTOR_CONFIG_PATH': str(config),
            'VECTOR_AUTH_FILE_OWNER': '{0}:{1}'.format(
                os.getuid(), os.getgid()
            ),
        }
        if extra_env:
            env.update(extra_env)
        result = subprocess.run(
            ['bash', str(SCRIPT)],
            env=env,
            capture_output=True,
            text=True,
        )
        return result.returncode, result.stderr


@unittest.skipUnless(sys.platform.startswith('linux') or os.getuid() != 0,
                     'chown to root:root needs a linux host or non-root user')
class BootstrapRemoteWriteAuthTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.target = Path(self._tmp.name) / 'remote-write-auth.json'

    def tearDown(self):
        self._tmp.cleanup()

    def test_migrates_static_basic_auth_pair(self):
        rc, stderr = run(STATIC_CONFIG, self.target)
        self.assertEqual(rc, 0, stderr)
        content = self.target.read_text()
        env = dict(
            line.split('=', 1) for line in content.splitlines()
            if line and not line.startswith('#')
        )
        self.assertEqual(env['VECTOR_AUTH_USER'], 'observability')
        self.assertEqual(env['VECTOR_AUTH_PASSWORD'], 'Bearer sekrit+value=/with/slash')

    def test_output_file_is_root_only(self):
        rc, stderr = run(STATIC_CONFIG, self.target)
        self.assertEqual(rc, 0, stderr)
        mode = stat.S_IMODE(self.target.stat().st_mode)
        self.assertEqual(mode, 0o600, 'credential file must not be group/world readable')

    def test_is_idempotent(self):
        rc, _ = run(STATIC_CONFIG, self.target)
        self.assertEqual(rc, 0)
        before = self.target.read_bytes()
        rc, _ = run(STATIC_CONFIG, self.target)
        self.assertEqual(rc, 0)
        self.assertEqual(self.target.read_bytes(), before, 'existing credential must not be rewritten')

    def test_refuses_exec_backed_credential(self):
        config = '''
[sinks.prometheus_remote.auth]
strategy = "basic"
user = "SECRET[agent_proxy_vault.user]"
password = "SECRET[agent_proxy_vault.password]"
'''
        rc, _ = run(config, self.target)
        self.assertEqual(rc, 3)
        self.assertFalse(self.target.exists(), 'must not fabricate a credential from SECRET[] refs')

    def test_returns_3_when_no_auth_block(self):
        rc, _ = run('[sinks.x]\ntype = "http"\n', self.target)
        self.assertEqual(rc, 3)
        self.assertFalse(self.target.exists())

    def test_returns_3_when_config_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
                'VECTOR_AUTH_STATIC_FILE': str(Path(tmp) / 'out.json'),
                'VECTOR_CONFIG_PATH': str(Path(tmp) / 'absent.toml'),
                'VECTOR_AUTH_FILE_OWNER': '{0}:{1}'.format(os.getuid(), os.getgid()),
            }
            result = subprocess.run(
                ['bash', str(SCRIPT)], env=env, capture_output=True, text=True
            )
            self.assertEqual(result.returncode, 3)


if __name__ == '__main__':
    unittest.main()
