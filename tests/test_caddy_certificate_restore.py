"""Real local PEM publication tests; no Vault, cloud or target host access."""
import importlib.util
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXECUTOR = ROOT / 'roles/docker/caddy_certificate_restore/files/restore.py'
spec = importlib.util.spec_from_file_location('restore', EXECUTOR)
restore = importlib.util.module_from_spec(spec)
spec.loader.exec_module(restore)


class CertificateRestoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.base = self.root / 'example.test'
        (self.base / 'versions').mkdir(parents=True, mode=0o700)
        self.fixture = self.root / 'fixture'
        self.fixture.mkdir()
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-keyout', str(self.fixture / 'key.pem'), '-out', str(self.fixture / 'cert.pem'),
                        '-days', '30', '-subj', '/CN=example.test'],
                       check=True, capture_output=True)
        for name in ('fullchain.pem', 'ca.pem', 'trust-bundle.pem'):
            shutil.copyfile(self.fixture / 'cert.pem', self.fixture / name)

    def tearDown(self):
        self.temp.cleanup()

    def stage(self):
        directory = Path(tempfile.mkdtemp(prefix='.restore-', dir=self.base / 'versions'))
        for name in restore.FILES:
            shutil.copyfile(self.fixture / name, directory / name)
        return directory

    def publish(self):
        self.assertTrue(restore.restore(self.stage(), self.base, 14 * 86400))
        return os.readlink(self.base / 'current')

    def test_atomic_idempotent_private_publication(self):
        original = self.publish()
        self.assertFalse(restore.restore(self.stage(), self.base, 14 * 86400))
        self.assertEqual(original, os.readlink(self.base / 'current'))
        self.assertEqual(0o600, stat.S_IMODE((self.base / 'current/key.pem').stat().st_mode))
        self.assertEqual(0o700, stat.S_IMODE((self.base / original).stat().st_mode))

    def test_malformed_input_preserves_current(self):
        original = self.publish()
        stage = self.stage()
        (stage / 'fullchain.pem').write_text('not a certificate')
        with self.assertRaises(subprocess.SubprocessError):
            restore.restore(stage, self.base, 0)
        self.assertEqual(original, os.readlink(self.base / 'current'))

    def test_mismatched_key_preserves_current(self):
        original = self.publish()
        stage = self.stage()
        subprocess.run(['openssl', 'genrsa', '-out', str(stage / 'key.pem'), '2048'],
                       check=True, capture_output=True)
        with self.assertRaises(ValueError):
            restore.restore(stage, self.base, 0)
        self.assertEqual(original, os.readlink(self.base / 'current'))

    def test_insufficient_validity_preserves_current(self):
        original = self.publish()
        with self.assertRaises(subprocess.SubprocessError):
            restore.restore(self.stage(), self.base, 31 * 86400)
        self.assertEqual(original, os.readlink(self.base / 'current'))

    def test_changed_bundle_cannot_overwrite_existing_generation(self):
        original = self.publish()
        stage = self.stage()
        with (stage / 'ca.pem').open('a') as output:
            output.write('\n')
        with self.assertRaises(ValueError):
            restore.restore(stage, self.base, 0)
        self.assertEqual(original, os.readlink(self.base / 'current'))

    def test_symlink_or_outside_stage_rejected(self):
        stage = self.stage()
        (stage / 'key.pem').unlink()
        (stage / 'key.pem').symlink_to(self.fixture / 'key.pem')
        with self.assertRaises(ValueError):
            restore.restore(stage, self.base, 0)
        with self.assertRaises(ValueError):
            restore.restore(self.fixture, self.base, 0)
        self.assertFalse((self.base / 'current').exists())

    def test_owner_does_not_fetch_credentials_or_restart_services(self):
        task = (ROOT / 'roles/docker/caddy_certificate_restore/tasks/main.yml').read_text()
        self.assertIn('no_log: true', task)
        self.assertIn('diff: false', task)
        self.assertNotIn('service:', task)
        self.assertNotIn('systemd:', task)
        self.assertNotIn('lookup(', task)


if __name__ == '__main__':
    unittest.main()
