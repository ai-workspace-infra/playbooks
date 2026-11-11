"""Auditor read access in vhosts/common: journald group and redacted Vector view."""
import json
import os
import re
import subprocess
import tempfile
import textwrap
import tomllib
import unittest
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parents[1]
COMMON = ROOT / 'roles/vhosts/common'
HELPER = COMMON / 'files/auditor-vector-view'

# Non-root CI cannot chown to root; shims drop the ownership flags only.
INSTALL_SHIM = """#!/usr/bin/env bash
args=()
while [ $# -gt 0 ]; do
  case "$1" in -o|-g) shift 2 ;; *) args+=("$1"); shift ;; esac
done
exec /usr/bin/env -u PATH /usr/bin/install "${args[@]}"
"""

FIXTURE = textwrap.dedent('''\
    [sinks.prometheus_remote]
    type = "prometheus_remote_write"
    endpoint = "https://observability.example/api/v1/write"
    tokens_per_second = 5
    [sinks.prometheus_remote.auth]
    strategy = "basic"
    user = "ingest"
    password = "fixture-inline-password"
    [sinks.billing]
    headers.Authorization = "Bearer fixture-bearer-value"
    [sinks.victorialogs]
    uri = "https://ingest:fixture-url-password@logs.example/insert"
    [secret.local]
    api_key = 'fixture-single-quoted'
    token: fixture-unquoted-token
    ''')


class AuditorVectorViewHelperTests(unittest.TestCase):
    def run_helper(self, src, dst):
        shim = Path(self.tmp.name) / 'bin'
        shim.mkdir(exist_ok=True)
        (shim / 'install').write_text(INSTALL_SHIM)
        (shim / 'chown').write_text('#!/bin/sh\nexit 0\n')
        for f in shim.iterdir():
            f.chmod(0o755)
        env = dict(os.environ, PATH=f"{shim}:{os.environ['PATH']}")
        return subprocess.run(['bash', str(HELPER), str(src), str(dst), 'auditor'],
                              env=env, check=True, capture_output=True, text=True)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src = Path(self.tmp.name) / 'etc-vector'
        self.dst = Path(self.tmp.name) / 'view'
        self.src.mkdir()
        (self.src / 'vector.toml').write_text(FIXTURE)
        (self.src / 'secrets.json').write_text('{"billing_token": "fixture-json"}')
        (self.src / 'vector.toml.1234.2026-11-11@00:00:00~').write_text(FIXTURE)

    def tearDown(self):
        self.tmp.cleanup()

    def test_redacts_every_credential_form_and_keeps_structure(self):
        out = self.run_helper(self.src, self.dst)
        self.assertIn('changed', out.stdout.split())
        view = (self.dst / 'vector.toml').read_text()
        self.assertNotIn('fixture-', view)
        self.assertIn('password = "<redacted>"', view)
        self.assertIn('https://<redacted>@logs.example/insert', view)
        self.assertIn('user = "ingest"', view)
        self.assertIn('tokens_per_second = 5', view)
        self.assertIn('endpoint = "https://observability.example/api/v1/write"', view)
        self.assertEqual(oct((self.dst / 'vector.toml').stat().st_mode & 0o777), '0o640')
        self.assertEqual(oct(self.dst.stat().st_mode & 0o777), '0o750')

    def test_skips_secret_files_and_backups_and_is_idempotent(self):
        self.run_helper(self.src, self.dst)
        self.assertEqual(['vector.toml'], sorted(p.name for p in self.dst.iterdir()))
        self.assertEqual('', self.run_helper(self.src, self.dst).stdout.strip())

    def test_removes_copies_whose_source_disappeared(self):
        self.run_helper(self.src, self.dst)
        (self.src / 'vector.toml').unlink()
        self.assertIn('changed', self.run_helper(self.src, self.dst).stdout.split())
        self.assertEqual([], list(self.dst.iterdir()))

    def test_missing_source_directory_publishes_an_empty_view(self):
        self.run_helper(Path(self.tmp.name) / 'absent', self.dst)
        self.assertEqual([], list(self.dst.iterdir()))


    def test_real_vector_template_with_inline_basic_auth_is_redacted_and_still_parses(self):
        env = Environment(loader=FileSystemLoader(ROOT / 'roles/vhosts/vector-agent/templates'))
        env.filters.update(bool=lambda v: str(v).lower() in ('true', '1', 'yes'), to_json=json.dumps,
                           regex_replace=lambda s, a, b: re.sub(a, b, s))
        template = env.from_string((ROOT / 'roles/vhosts/vector-agent/templates/vector.toml.j2').read_text())
        rendered = template.render(groups={}, inventory_hostname='edge.example.test',
                                   vector_observability_node_identity='node.example.test',
                                   vector_observability_environment='uat', vector_observability_provider='test',
                                   vector_observability_service_domain='edge.example.test',
                                   vector_billing_ingest_enabled=False, blackbox_ssl_targets=[],
                                   blackbox_exporter_role_defaults={'blackbox_ssl_targets': []},
                                   vector_system_journald_enabled=True, vector_xhttp_monitoring_enabled=False,
                                   vector_system_journald_units=['xray.service'],
                                   vector_auth_user='ingest', vector_auth_password='fixture-template-password')
        self.assertIn('fixture-template-password', rendered)
        (self.src / 'vector.toml').write_text(rendered)
        self.run_helper(self.src, self.dst)
        view = (self.dst / 'vector.toml').read_text()
        self.assertNotIn('fixture-template-password', view)
        config = tomllib.loads(view)
        self.assertEqual('<redacted>', config['sinks']['prometheus_remote']['auth']['password'])
        self.assertEqual('ingest', config['sinks']['prometheus_remote']['auth']['user'])

class AuditorReadAccessContractTests(unittest.TestCase):
    def test_journald_group_is_declared_without_adm(self):
        defaults = yaml.safe_load((COMMON / 'defaults/main.yml').read_text())
        self.assertEqual(['systemd-journal'], defaults['common_os_identity_read_groups']['auditor'])
        self.assertNotIn('adm', defaults['common_os_identity_read_groups']['auditor'])
        self.assertNotIn('operator', defaults['common_os_identity_read_groups'])

    def test_group_assertion_and_user_module_consume_the_same_declaration(self):
        tasks = yaml.safe_load((COMMON / 'tasks/os_identity.yml').read_text())
        guard = next(t for t in tasks if t['name'] == 'OS identities | reject unexpected group membership')
        self.assertTrue(any('common_os_identity_read_groups' in rule for rule in guard['ansible.builtin.assert']['that']))
        user = next(t for t in tasks if t['name'].startswith('OS identities | create role accounts'))
        self.assertIn('common_os_identity_read_groups', user['ansible.builtin.user']['groups'])
        self.assertFalse(user['ansible.builtin.user']['append'])

    def test_read_access_runs_after_auditor_deny_probes(self):
        tasks = yaml.safe_load((COMMON / 'tasks/os_identity.yml').read_text())
        names = [t['name'] for t in tasks]
        include = names.index('OS identities | grant auditor read-only journald and redacted Vector configuration')
        self.assertGreater(include, names.index('OS identities | verify auditor cannot use sudo'))
        self.assertEqual('auditor_read_access.yml', tasks[include]['ansible.builtin.include_tasks'])

    def test_source_vector_files_are_never_chmodded_by_common(self):
        text = (COMMON / 'tasks/auditor_read_access.yml').read_text()
        tasks = yaml.safe_load(text)
        for task in tasks:
            for module in ('ansible.builtin.file', 'ansible.builtin.copy', 'ansible.builtin.template'):
                if module in task:
                    self.assertNotIn('source_dir', str(task[module].get('path', task[module].get('dest', ''))))
        self.assertNotIn('ansible.posix.acl', text)


if __name__ == '__main__':
    unittest.main()
