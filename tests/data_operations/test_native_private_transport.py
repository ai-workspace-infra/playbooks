"""Synthetic credential transport only; no Docker, Vault or managed DB access."""
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

OWNER = Path(__file__).resolve().parents[2] / 'scripts/data_operations/selfhost'
sys.path.insert(0, str(OWNER))
import native_init_host as HOST
sys.path.pop(0)
REAL_DIRECTORY = tempfile.TemporaryDirectory


def credentials():
    return dict(postgres_password='synthetic$@:', ghcr_username='fixture', ghcr_token='synthetic-registry')


class PrivateTransportTests(unittest.TestCase):
    def test_input_requires_exact_private_shape_and_line_bound(self):
        accepted = json.dumps(credentials()) + '\n'
        with patch.object(sys, 'stdin', io.StringIO(accepted)):
            self.assertEqual(HOST.read_credentials(), credentials())
        for value in ('', accepted.rstrip(), '{}\n', json.dumps(dict(credentials(), source_dsn='forbidden'))+'\n',
                      json.dumps(dict(credentials(), postgres_password='bad\nline'))+'\n', 'x'*65537+'\n'):
            with self.subTest(length=len(value)), patch.object(sys, 'stdin', io.StringIO(value)):
                with self.assertRaises((HOST.Refused, ValueError)):
                    HOST.read_credentials()

    def registry_case(self, failure):
        directories = []
        def temporary(**kwargs):
            self.assertEqual(kwargs['dir'], '/dev/shm')
            # Synthetic substitute only; the real runtime requires findmnt
            # to prove tmpfs. No production credential enters this fixture.
            result = REAL_DIRECTORY(prefix='synthetic-volatile-')
            directories.append(Path(result.name))
            return result
        def command(argv, **kwargs):
            if argv[0] == 'findmnt':
                self.assertEqual(argv[-1], '/dev/shm')
                return 'tmpfs'
            self.assertEqual(argv, ['docker','login','ghcr.io','--username','fixture','--password-stdin'])
            self.assertEqual(kwargs['input'], credentials()['ghcr_token'])
            directory = Path(kwargs['env']['DOCKER_CONFIG'])
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
            (directory/'config.json').write_text('synthetic registry fixture')
            if failure == 'login':
                raise HOST.Refused('fixture failure')
            return ''
        with patch.object(HOST.tempfile,'TemporaryDirectory',side_effect=temporary), patch.object(HOST,'command',side_effect=command):
            if failure:
                with self.assertRaises(HOST.Refused):
                    with HOST.registry_session(credentials()) as env:
                        self.assertEqual(env['DOCKER_CONFIG'],str(directories[0]))
                        raise HOST.Refused('fixture execution failure')
            else:
                with HOST.registry_session(credentials()) as env:
                    self.assertTrue((Path(env['DOCKER_CONFIG'])/'config.json').exists())
        self.assertEqual(len(directories),1)
        self.assertFalse(directories[0].exists())

    def test_registry_memory_session_cleanup_success_and_failures(self):
        for failure in ('','login','execution'):
            with self.subTest(failure=failure):
                self.registry_case(failure)

    def test_registry_rejects_nonvolatile_storage(self):
        with patch.object(HOST,'command',return_value='ext4'), patch.object(HOST.tempfile,'TemporaryDirectory') as temporary:
            with self.assertRaises(HOST.Refused):
                with HOST.registry_session(credentials()):
                    self.fail('nonvolatile registry accepted')
            temporary.assert_not_called()

    def test_owned_container_is_removed_after_timeout_or_failure(self):
        for failure in (HOST.Refused('failure'), TimeoutError('timeout')):
            with self.subTest(kind=type(failure).__name__), patch.object(HOST,'command',side_effect=failure), patch.object(HOST,'remove_execution_container') as cleanup:
                with self.assertRaises(type(failure)):
                    HOST.run_tool('fixture-image','owned-fixture',['init'],credentials(),{})
                cleanup.assert_called_once_with('owned-fixture')

    def test_tool_only_receives_secret_via_stdin(self):
        with patch.object(HOST,'command',return_value='{}') as command, patch.object(HOST,'remove_execution_container'):
            HOST.run_tool('fixture-image','owned-fixture',['init'],credentials(),{'DOCKER_CONFIG':'/volatile/fixture'})
            args, kwargs = command.call_args
            argv = args[0]
            self.assertNotIn('--env-file',argv)
            self.assertNotIn('--env',argv)
            self.assertNotIn('synthetic',json.dumps(argv))
            self.assertNotIn('postgresql://',json.dumps(argv))
            self.assertEqual(argv[argv.index('--entrypoint')+1],'/bin/sh')
            self.assertIn(HOST.TARGET_BOOTSTRAP,argv)
            self.assertEqual(kwargs['input'],'postgresql://postgres:synthetic%24%40%3A@127.0.0.1:5432/account?sslmode=disable\n')
            self.assertNotIn('NATIVE_TARGET_DSN',kwargs['env'])

    def test_role_stdin_and_shared_ssh_pipelining(self):
        for role in ('web_saas_native_init','web_saas_native_billing_upgrade'):
            content=(OWNER.parents[2]/'roles'/role/'tasks/main.yml').read_text()
            self.assertIn('stdin_add_newline: true',content)
            self.assertIn('postgres_password',content)
            self.assertNotIn('NATIVE_POSTGRES_PASSWORD',content)
            self.assertNotIn('docker login',content)
        self.assertIn('export ANSIBLE_PIPELINING=true',(OWNER/'native_access_guard.sh').read_text())
        self.assertIn('export ANSIBLE_KEEP_REMOTE_FILES=false',(OWNER/'native_access_guard.sh').read_text())


if __name__=='__main__':
    unittest.main()
