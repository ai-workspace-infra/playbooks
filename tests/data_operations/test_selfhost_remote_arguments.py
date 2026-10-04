"""SSH must retain empty optional release/version arguments on a read-only probe."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class RemoteArgumentsTests(unittest.TestCase):
    def test_optional_arguments_survive_remote_shell_parsing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / 'bin'
            binary.mkdir()
            ssh = binary / 'ssh'
            ssh.write_text('#!/bin/bash\nprintf "%s\\n" "${!#}"\ncat >/dev/null\n')
            ssh.chmod(0o755)
            cmdb = root / 'cmdb.json'
            cmdb.write_text(json.dumps({'environment': 'uat', 'web-saas-uat':
                                       {'ip': '192.0.2.1', 'ansible_user': 'root', 'groups': ['web_saas']}}))
            env = dict(os.environ, PATH=str(binary) + ':' + os.environ['PATH'],
                       REQUESTED_ENVIRONMENT='uat', CMDB_FILE=str(cmdb), RELEASE_TAG='',
                       EXPECTED_SCHEMA_VERSION='', CONFIG_JSON='{"target_host":"web-saas-uat","acceptance_run_id":"probe-test"}')
            result = subprocess.run(['bash', str(ROOT / 'scripts/data_operations/selfhost/acceptance.sh'), 'probe'],
                                    env=env, capture_output=True, text=True, check=True)
            self.assertEqual(shlex.split(result.stdout.strip()), ['bash', '-s', '--', 'probe', 'probe-test', '', ''])


if __name__ == '__main__':
    unittest.main()
