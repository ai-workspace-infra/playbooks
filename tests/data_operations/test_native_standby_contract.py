"""Fictional host guards and source contracts; never PROD acceptance."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[2]


class NativeStandbyTests(unittest.TestCase):
    def test_only_postgres_is_started_from_fixed_gitops_projection(self):
        plays = yaml.safe_load((ROOT / 'setup-web-saas-native-standby.yml').read_text())
        self.assertEqual(plays[0]['hosts'], 'web-saas-prod')
        self.assertEqual(plays[0]['roles'], ['roles/vhosts/docker', 'roles/web_saas_prod_storage', 'roles/web_saas_native_standby'])
        tasks = yaml.safe_load((ROOT / 'roles/web_saas_native_standby/tasks/main.yml').read_text())
        starts = [task['ansible.builtin.command']['argv'] for task in tasks
                  if 'ansible.builtin.command' in task and 'up' in task['ansible.builtin.command'].get('argv', [])]
        self.assertEqual(len(starts), 2)
        self.assertEqual(starts[0][-5:], ['postgres-only.json', 'up', '-d', '--no-deps', 'postgres'])
        self.assertEqual(starts[1][-6:], ['postgres-only.json', 'up', '-d', '--no-deps', '--force-recreate', 'postgres'])
        recreate = next(task for task in tasks if 'Recreate only a managed' in task['name'])
        self.assertEqual(recreate['when'], 'native_repair_missing_password | bool')
        text = (ROOT / 'roles/web_saas_native_standby/tasks/main.yml').read_text()
        for item in ('--no-env-resolution', 'native_gitops_head.stdout', 'server_version_num',
                     '127.0.0.1', 'schema_initialized: false', 'database_cutover_approved: false',
                     'Existing PostgreSQL image differs', 'ownership.json'):
            self.assertIn(item, text)
        self.assertNotIn('roles/vhosts/Doco-CD', str(plays))
        self.assertNotIn('initialize-web-saas-schemas', text)
        self.assertLess(text.index('Qualify the pulled PostgreSQL binary'), text.index('Start only PostgreSQL'))
        self.assertIn('--network, none, --entrypoint, postgres', text)
        self.assertLess(text.index('Resolve postgres UID and GID'), text.index('Start only PostgreSQL'))
        self.assertIn("mode: '0700'", text)
        self.assertIn('follow: false', text)
        self.assertNotIn('recurse: true', text)
        self.assertIn('name: native_host_defaults', text)
        self.assertIn('Refusing credential changes on an unowned', text)
        self.assertIn('not (native_startup_diagnostic.stdout | from_json).pg_version_present', text)

    def test_target_is_checked_before_disk_or_host_mutation(self):
        text = (ROOT / 'setup-web-saas-native-standby.yml').read_text()
        for item in ('open-platform-prod', 'native_authoritative_cmdb', 'ansible_user',
                     'GITOPS_COMMIT', 'POSTGRES_PASSWORD'):
            self.assertIn(item, text)
        self.assertLess(text.index('Require the canonical resource identity'), text.index('roles:'))
        self.assertLess(text.index('Refuse active writers'), text.index('roles:'))
        guard = (ROOT / 'scripts/data_operations/selfhost/init_guard_host.sh').read_text()
        self.assertIn("t.typtype IN ('e','d','r','m')", guard)

    def writer_guard(self, names='', state='false:no', fail=False):
        with tempfile.TemporaryDirectory() as d:
            binary = Path(d) / 'docker'
            binary.write_text('''#!/usr/bin/env bash
set -euo pipefail
if [[ "${TEST_DOCKER_FAIL:-}" == true ]]; then exit 1; fi
if [[ "$1" == ps ]]; then printf '%s\\n' "$TEST_DOCKER_NAMES"; else printf '%s\\n' "$TEST_DOCKER_STATE"; fi
''')
            binary.chmod(0o700)
            env = dict(os.environ, PATH=d + ':' + os.environ['PATH'],
                       TEST_DOCKER_NAMES=names, TEST_DOCKER_STATE=state,
                       TEST_DOCKER_FAIL='true' if fail else 'false')
            return subprocess.run(['bash', str(ROOT / 'scripts/data_operations/selfhost/native_writer_guard_host.sh')],
                                  env=env, capture_output=True, text=True)

    def test_empty_host_and_only_postgres_are_eligible(self):
        for names in ('', 'web-saas-postgresql', 'web-saas-postgresql\nweb-saas-accounts'):
            self.assertEqual(self.writer_guard(names).returncode, 0)

    def test_running_writers_or_restart_policies_are_refused(self):
        for name in ('web-saas-accounts', 'web-saas-billing', 'web-saas-xworkmate-bridge', 'doco-cd', 'doco_cd-controller'):
            for state in ('true:no', 'false:always', 'false:unless-stopped'):
                self.assertNotEqual(self.writer_guard(name, state).returncode, 0)

    def test_caddy_remains_running_without_weakening_writer_guard(self):
        self.assertEqual(self.writer_guard('web-saas-caddy', 'true:unless-stopped').returncode, 0)
        for name in ('web-saas-accounts', 'web-saas-billing', 'doco-cd', 'web-saas-caddy-writer'):
            self.assertNotEqual(self.writer_guard('web-saas-caddy\n' + name, 'true:unless-stopped').returncode, 0)

    def test_docker_query_failure_is_not_an_empty_host(self):
        self.assertNotEqual(self.writer_guard(fail=True).returncode, 0)


if __name__ == '__main__':
    unittest.main()
