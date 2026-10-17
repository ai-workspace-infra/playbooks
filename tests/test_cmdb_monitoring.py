import json
from pathlib import Path
import tomllib
import unittest

from jinja2 import Environment, StrictUndefined
import yaml


ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / 'roles/vhosts/cmdb-monitoring'


class IsolatedMonitoringTest(unittest.TestCase):
    def test_credentials_round_trip_without_toml_or_vrl_injection(self):
        env = Environment(undefined=StrictUndefined)
        env.filters['to_json'] = json.dumps
        values = dict(
            cmdb_monitoring_instance='node-"test\\name',
            cmdb_monitoring_provider='gcp',
            cmdb_monitoring_project_id='open-platform-shared-510113',
            cmdb_monitoring_logical_project='open-platform-shared',
            cmdb_monitoring_environment='shared',
            cmdb_monitoring_endpoint='https://observability.svc.plus/ingest/metrics/api/v1/write',
            cmdb_monitoring_auth_user='test-user',
            cmdb_monitoring_auth_password='quote" slash\\ newline\nvalue',
        )
        rendered = env.from_string((ROLE / 'templates/vector.toml.j2').read_text()).render(values)
        config = tomllib.loads(rendered)
        self.assertEqual(config['sinks']['metrics']['auth']['password'], values['cmdb_monitoring_auth_password'])
        assignments = config['transforms']['labels']['source'].splitlines()
        labels = {line.split(' = ', 1)[0]: json.loads(line.split(' = ', 1)[1])
                  for line in assignments if ' = ' in line}
        self.assertEqual(labels['.tags.instance'], values['cmdb_monitoring_instance'])
        self.assertEqual(labels['.tags.logical_project'], 'open-platform-shared')
        self.assertNotEqual(labels['.tags.project_id'], labels['.tags.logical_project'])
        self.assertEqual(set(config['sources']), {'node'})

    def test_units_keep_exporter_private_and_restart_only_isolated_services(self):
        exporter = (ROLE / 'templates/cmdb-node-exporter.service.j2').read_text()
        agent = (ROLE / 'templates/cmdb-metrics-agent.service.j2').read_text()
        self.assertIn('--web.listen-address=127.0.0.1:19100', exporter)
        for unit in (exporter, agent):
            self.assertIn('DynamicUser=yes', unit)
            self.assertIn('CPUQuota=10%', unit)
            self.assertIn('NoNewPrivileges=yes', unit)
        self.assertIn('LoadCredential=vector.toml:', agent)
        handlers = yaml.safe_load((ROLE / 'handlers/main.yml').read_text())
        self.assertEqual({h['ansible.builtin.systemd_service']['name'] for h in handlers},
                         {'cmdb-node-exporter', 'cmdb-metrics-agent'})

    def test_credentials_hidden_and_archives_verified_before_extraction(self):
        tasks = yaml.safe_load((ROLE / 'tasks/main.yml').read_text())[2]['block']
        download = next(t for t in tasks if 'ansible.builtin.get_url' in t)
        extract = next(t for t in tasks if 'ansible.builtin.unarchive' in t)
        self.assertLess(tasks.index(download), tasks.index(extract))
        self.assertTrue(download['ansible.builtin.get_url']['checksum'].startswith('sha256:'))
        config = next(t for t in tasks if t.get('ansible.builtin.template', {}).get('dest') == '/etc/cmdb-monitoring/vector.toml')
        self.assertTrue(config['no_log'])
        self.assertFalse(config['diff'])
        self.assertEqual(config['ansible.builtin.template']['mode'], '0600')
        self.assertIn('validate', config['ansible.builtin.template'])


if __name__ == '__main__':
    unittest.main()
