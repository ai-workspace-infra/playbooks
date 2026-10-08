"""PROD repair remains application-only and fails closed on data/traffic changes."""
from pathlib import Path
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]


class ProdServicesContract(unittest.TestCase):
    def test_selection_and_existing_database_guards(self):
        play = yaml.safe_load((ROOT / 'converge-web-saas-prod-services.yml').read_text())[0]
        self.assertEqual(play['hosts'], 'web-saas-prod')
        assertions = ' '.join(str(t.get('ansible.builtin.assert', {})) for t in play['pre_tasks'])
        for guard in ('CMDB_FILE', 'desired_active', 'cutover', 'initialization', 'migration',
                      'background_writers', 'mount_path', 'schema_management'):
            self.assertIn(guard, assertions)

    def test_no_data_initialization_or_database_recreation(self):
        text = (ROOT / 'roles/web_saas_prod_services/tasks/main.yml').read_text()
        for forbidden in ('DROP DATABASE', 'DROP TABLE', 'TRUNCATE ', 'ALTER ROLE',
                          'CREATE DATABASE', 'CREATE TABLE', 'docker compose down', 'pg_restore'):
            self.assertNotIn(forbidden, text)
        self.assertIn('CREATE ROLE account_user', text)
        self.assertIn('WHERE NOT EXISTS', text)
        self.assertIn('.State.StartedAt', text)
        self.assertIn('.Mounts', text)
        self.assertIn('status_code: 200', text)

    def test_secret_templates_and_ingress_fail_closed(self):
        tasks = yaml.safe_load((ROOT / 'roles/web_saas_prod_services/tasks/main.yml').read_text())
        for task in tasks:
            if task['name'].startswith(('Read runtime', 'Resolve the existing Vault',
                                        'Create the missing', 'Render only application',
                                        'Restore the existing stunnel', 'Publish the validated')):
                self.assertTrue(task.get('no_log'), task['name'])
        ingress = (ROOT / 'roles/web_saas_prod_services/templates/services.caddy.j2').read_text()
        self.assertIn('remote_ip', ingress)
        self.assertIn('403', ingress)
        self.assertNotIn('3000', ingress)
        self.assertNotIn('5432', ingress)


if __name__ == '__main__':
    unittest.main()
