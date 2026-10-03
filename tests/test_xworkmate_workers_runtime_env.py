"""Keyless runtime bootstrap tests; temporary filesystem and modeled root ownership."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from jinja2 import Environment, StrictUndefined

ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / 'roles/vhosts/xworkmate_workers'
spec = importlib.util.spec_from_file_location('worker_runtime_env', ROLE / 'files/xworkmate-worker-runtime-env.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class WorkerRuntimeEnvironmentTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.run = self.root / 'run'
        self.etc = self.root / 'etc'
        self.output = self.run / 'xworkmate-workers'
        self.source = self.run / 'aggregator/client-key'
        self.config = self.etc / 'xworkmate-workers/runtime-env-source.json'
        for directory in [self.run, self.etc, self.source.parent, self.config.parent]:
            directory.mkdir(mode=0o755, parents=True, exist_ok=True)
            directory.chmod(0o755)
        self.source.write_text('test-safe-client-key\n')
        self.source.chmod(0o600)
        self.config.write_text(json.dumps({'keySourceFile': str(self.source), 'gatewayUser': 'gateway-test'}))
        self.config.chmod(0o600)
        self.owners = {}
        self.fdpaths = {}
        self.gateway_uid, self.gateway_gid = os.getuid(), os.getgid()
        native_lstat, native_fstat, native_open = Path.lstat, os.fstat, os.open
        def metadata(value, path):
            uid = self.owners.get(path, self.gateway_uid if path.parent == self.output and path.name in {'model.env', 'opencode.env'} else 0)
            return SimpleNamespace(**{name: getattr(value, name) for name in ('st_mode', 'st_gid', 'st_dev', 'st_ino', 'st_nlink', 'st_size')}, st_uid=uid)
        def opened(path, flags, mode=0o777, **kwargs):
            fd = native_open(path, flags, mode, **kwargs)
            self.fdpaths[fd] = Path(path)
            return fd
        def fchown(fd, uid, gid):
            self.owners[self.fdpaths[fd]] = uid
        patches = [patch.object(helper, 'RUN_ROOT', self.run), patch.object(helper, 'ETC_ROOT', self.etc),
                   patch.object(helper, 'CONFIG_PATH', self.config), patch.object(helper, 'OUTPUT_DIR', self.output),
                   patch.object(os, 'geteuid', return_value=0), patch.object(os, 'open', side_effect=opened),
                   patch.object(Path, 'lstat', lambda path: metadata(native_lstat(path), path)),
                   patch.object(os, 'fstat', lambda fd: metadata(native_fstat(fd), self.fdpaths[fd])),
                   patch.object(os, 'fchown', side_effect=fchown), patch.object(os, 'chown'),
                   patch.object(helper.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=self.gateway_uid, pw_gid=self.gateway_gid))]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def test_atomic_private_env_and_existing_password_preserved_on_key_reload(self):
        report = helper.initialize()
        self.assertTrue(report['modelKeyPresent'])
        self.assertFalse(report['opencodePasswordPreserved'])
        model = self.output / 'model.env'
        password = self.output / 'opencode.env'
        self.assertEqual(model.read_text(), 'XWORKMATE_LLM_API_KEY=test-safe-client-key\n')
        original = password.read_text()
        self.assertRegex(original, r'^OPENCODE_PASSWORD=[0-9a-f]{64}\n$')
        inode = password.stat().st_ino
        self.source.write_text('new-safe-client-key')
        self.assertTrue(helper.initialize()['opencodePasswordPreserved'])
        self.assertEqual(password.read_text(), original)
        self.assertEqual(password.stat().st_ino, inode)
        self.assertEqual(model.read_text(), 'XWORKMATE_LLM_API_KEY=new-safe-client-key\n')
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o710)
        for path in [model, password]:
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.lstat().st_uid, self.gateway_uid)
        self.assertEqual(sorted(path.name for path in self.output.iterdir()), ['.bootstrap.lock', 'model.env', 'opencode.env'])
        self.assertNotIn('test-safe-client-key', json.dumps(report))

    def test_unsafe_source_literals_fail_without_secret_output_or_env(self):
        for value in ['', 'private-test\nsecond', 'private-test\n\n', 'private-test\r\n', 'private-test\x00',
                      'private test', 'private-test$', 'private-test\\', 'private-test"', 'é', 'a' * 8193]:
            with self.subTest(case=value[:12]):
                self.source.write_text(value)
                stdout, stderr = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    self.assertEqual(helper.main(), 1)
                self.assertEqual(stdout.getvalue(), '')
                self.assertEqual(stderr.getvalue(), 'XWorkmate runtime environment initialization failed.\n')
                self.assertFalse((self.output / 'model.env').exists())

    def test_source_ownership_mode_symlink_and_parent_permissions_are_rejected(self):
        self.owners[self.source] = self.gateway_uid
        with self.assertRaises(ValueError): helper.initialize()
        self.owners.pop(self.source)
        self.source.chmod(0o640)
        with self.assertRaises(ValueError): helper.initialize()
        self.source.chmod(0o600)
        self.source.parent.chmod(0o775)
        with self.assertRaises(ValueError): helper.initialize()
        self.source.parent.chmod(0o755)
        target = self.source.with_name('other-key')
        target.write_text('safe-token')
        target.chmod(0o600)
        self.source.unlink()
        self.source.symlink_to(target)
        with self.assertRaises(ValueError): helper.initialize()

    def test_parent_symlink_hardlink_and_special_mode_source_are_rejected(self):
        self.source.chmod(0o4600)
        with self.assertRaises(ValueError): helper.initialize()
        self.source.chmod(0o600)
        alias = self.source.with_name('hard-link')
        os.link(self.source, alias)
        with self.assertRaises(ValueError): helper.initialize()
        alias.unlink()
        original = self.source.parent
        renamed = original.with_name('renamed')
        original.rename(renamed)
        original.symlink_to(renamed, target_is_directory=True)
        with self.assertRaises(ValueError): helper.initialize()

    def test_atomic_failure_cleans_temporary_file_and_never_logs_secret(self):
        helper.initialize()
        original = (self.output / 'model.env').read_text()
        self.source.write_text('replacement-secret-test')
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(os, 'replace', side_effect=OSError('replacement-secret-test')):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                self.assertEqual(helper.main(), 1)
        self.assertEqual(stdout.getvalue(), '')
        self.assertEqual(stderr.getvalue(), 'XWorkmate runtime environment initialization failed.\n')
        self.assertEqual((self.output / 'model.env').read_text(), original)
        self.assertEqual(sorted(path.name for path in self.output.iterdir()), ['.bootstrap.lock', 'model.env', 'opencode.env'])

    def test_config_is_fixed_root_owned_and_does_not_allow_arbitrary_path_or_keys(self):
        self.owners[self.config] = self.gateway_uid
        with self.assertRaises(ValueError): helper.initialize()
        self.owners.pop(self.config)
        with patch.object(os, 'geteuid', return_value=self.gateway_uid):
            with self.assertRaises(ValueError): helper.initialize()
        for source in [str(self.root / 'elsewhere'), str(self.run / 'aggregator/../aggregator/client-key')]:
            self.config.write_text(json.dumps({'keySourceFile': source, 'gatewayUser': 'gateway-test'}))
            with self.assertRaises(ValueError): helper.initialize()
        self.config.write_text(json.dumps({'keySourceFile': str(self.source), 'gatewayUser': 'gateway-test', 'output': '/tmp/escape'}))
        with self.assertRaises(ValueError): helper.initialize()
        self.assertFalse(self.output.exists())

    def test_invalid_existing_password_or_output_symlink_never_replaces_model_key(self):
        helper.initialize()
        password = self.output / 'opencode.env'
        password.write_text('OPENCODE_PASSWORD=invalid\n')
        self.source.write_text('replacement-key')
        with self.assertRaises(ValueError): helper.initialize()
        self.assertEqual((self.output / 'model.env').read_text(), 'XWORKMATE_LLM_API_KEY=test-safe-client-key\n')
        password.write_text('OPENCODE_PASSWORD=' + 'a' * 64 + '\n')
        password.chmod(0o644)
        with self.assertRaises(ValueError): helper.initialize()
        password.chmod(0o600)
        password.unlink()
        password.symlink_to(self.source)
        with self.assertRaises(ValueError): helper.initialize()

    def test_templates_have_only_nonsecret_config_and_explicit_bootstrap_order(self):
        env = Environment(undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True)
        env.filters['to_json'] = json.dumps
        values = {'xworkmate_workers_model_key_source_file': '/run/aggregator/client-key',
                  'xworkmate_workers_gateway_user': 'gateway-test',
                  'xworkmate_workers_model_key_source_unit': 'ai-aggregator-vault.service'}
        config = json.loads(env.from_string((ROLE / 'templates/runtime-env-source.json.j2').read_text()).render(values))
        self.assertEqual(config, {'keySourceFile': '/run/aggregator/client-key', 'gatewayUser': 'gateway-test'})
        unit = env.from_string((ROLE / 'templates/runtime-env.service.j2').read_text()).render(values)
        for expected in ['Type=oneshot', 'RemainAfterExit=yes', 'After=ai-aggregator-vault.service',
                         'Requires=ai-aggregator-vault.service', 'Before=xworkmate-opencode.service',
                         'User=root', 'ExecStart=/usr/local/libexec/xworkmate-worker-runtime-env']:
            self.assertIn(expected, unit)
        self.assertNotIn('EnvironmentFile=', unit)
        self.assertNotIn('XWORKMATE_LLM_API_KEY=', unit)
        self.assertNotIn('OPENCODE_PASSWORD=', unit)


if __name__ == '__main__':
    unittest.main()
