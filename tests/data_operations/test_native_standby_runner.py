"""Same-run host caller fixtures. Never connects to a real host."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'scripts/data_operations/selfhost/native_standby_runner.sh'


class NativeRunnerTests(unittest.TestCase):
    def fixture(self, directory):
        p = Path(directory)
        cmdb = p / 'cmdb'
        cmdb.mkdir()
        file = cmdb / 'cmdb.json'
        file.write_text(json.dumps({'web-saas-prod': {'ip': '192.0.2.1', 'ansible_user': 'fixture'}}))
        (cmdb / 'inventory.ini').write_text('[web_saas]\nweb-saas-prod ansible_host=192.0.2.1\n')
        access = p / 'prod-native-access-123-1'
        access.mkdir()
        (access / 'id_ed25519').write_text('fictional-key-never-used')
        (access / 'access.json').write_text(json.dumps({'instance': 'web-saas-prod',
            'project': 'open-platform-prod', 'target_ip': '192.0.2.1', 'ssh_user': 'fixture',
            'private_key': str(access / 'id_ed25519')}))
        binary = p / 'prod-native-ansible/bin'
        binary.mkdir(parents=True)
        command = binary / 'ansible-playbook'
        command.write_text('''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
Path(os.environ['RUNNER_TEMP'],'host-invocation.json').write_text(json.dumps(sys.argv[1:]))
Path(os.environ['RUNNER_TEMP'],'host-ssh-policy.json').write_text(json.dumps({k:os.environ[k] for k in ['ANSIBLE_HOST_KEY_CHECKING','ANSIBLE_SSH_ARGS','ANSIBLE_SSH_COMMON_ARGS']}))
r={'stage':'database_standby','host':'web-saas-prod','environment':'prod',
   'gitops_commit':os.environ['GITOPS_COMMIT'],'postgres_major':17,
   'independent_disk_verified':True,'writers_paused':True,
   'schema_initialized':False,'database_cutover_approved':False}
if os.environ.get('FICTIONAL_BAD_RECEIPT'): r['gitops_commit']='0'*40
Path(os.environ['NATIVE_RECEIPT_FILE']).write_text(json.dumps(r))
''')
        command.chmod(0o700)
        return dict(os.environ, RUNNER_TEMP=str(p), CMDB_DIR=str(cmdb),
            EXPECTED_CMDB_SHA256=hashlib.sha256(file.read_bytes()).hexdigest(),
            NATIVE_ACCESS_FILE=str(access / 'access.json'), GITHUB_RUN_ID='123',
            GITHUB_RUN_ATTEMPT='1', GITOPS_CHECKOUT=str(p), GITOPS_COMMIT='a' * 40,
            NATIVE_RECEIPT_FILE=str(p / 'prod-native-standby-receipt.json'))

    def execute(self, env):
        return subprocess.run(['bash', str(SCRIPT)], env=env, capture_output=True, text=True)

    def test_original_inventory_and_exact_ephemeral_key_are_consumed(self):
        with tempfile.TemporaryDirectory() as d:
            env = self.fixture(d)
            result = self.execute(env)
            self.assertEqual(result.returncode, 0, result.stderr)
            args = json.loads((Path(d) / 'host-invocation.json').read_text())
            self.assertEqual(args, ['-i', env['CMDB_DIR'] + '/inventory.ini', '--limit', 'web-saas-prod',
                '--private-key', str(Path(d) / 'prod-native-access-123-1/id_ed25519'),
                'setup-web-saas-native-standby.yml'])
            policy = json.loads((Path(d) / 'host-ssh-policy.json').read_text())
            self.assertEqual(policy['ANSIBLE_HOST_KEY_CHECKING'], 'true')
            self.assertEqual(policy['ANSIBLE_SSH_ARGS'], '-o ControlMaster=no -o ControlPersist=no')
            self.assertIn('StrictHostKeyChecking=accept-new', policy['ANSIBLE_SSH_COMMON_ARGS'])
            self.assertNotIn('StrictHostKeyChecking=no', policy['ANSIBLE_SSH_COMMON_ARGS'])

    def test_bad_checksum_or_foreign_run_refuses_before_host(self):
        for key, value in [('EXPECTED_CMDB_SHA256', '0' * 64), ('GITHUB_RUN_ATTEMPT', '2'),
                           ('NATIVE_RECEIPT_FILE', '/tmp/foreign-receipt.json')]:
            with tempfile.TemporaryDirectory() as d:
                env = self.fixture(d)
                env[key] = value
                self.assertNotEqual(self.execute(env).returncode, 0)
                self.assertFalse((Path(d) / 'host-invocation.json').exists())

    def test_stale_receipt_refuses_before_host(self):
        with tempfile.TemporaryDirectory() as d:
            env = self.fixture(d)
            Path(env['NATIVE_RECEIPT_FILE']).write_text('{}')
            self.assertNotEqual(self.execute(env).returncode, 0)
            self.assertFalse((Path(d) / 'host-invocation.json').exists())

    def test_wrong_host_identity_refuses_before_host(self):
        with tempfile.TemporaryDirectory() as d:
            env = self.fixture(d)
            file = Path(env['NATIVE_ACCESS_FILE'])
            data = json.loads(file.read_text())
            data['ssh_user'] = 'other'
            file.write_text(json.dumps(data))
            self.assertNotEqual(self.execute(env).returncode, 0)
            self.assertFalse((Path(d) / 'host-invocation.json').exists())

    def test_foreign_gitops_receipt_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            env = self.fixture(d)
            env['FICTIONAL_BAD_RECEIPT'] = 'true'
            self.assertNotEqual(self.execute(env).returncode, 0)


if __name__ == '__main__':
    unittest.main()
