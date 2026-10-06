"""Evaluate real Ansible conditions against Docker error fixtures, no host API."""
from pathlib import Path
import unittest
from jinja2 import Environment
import yaml

ROOT = Path(__file__).resolve().parents[2]


class MissingPostgresTests(unittest.TestCase):
    def conditions(self):
        for role, variable in [('web_saas_prod_storage', 'web_saas_prod_storage_postgres'),
                              ('web_saas_native_standby', 'native_existing_postgres_image')]:
            tasks = yaml.safe_load((ROOT / 'roles' / role / 'tasks/main.yml').read_text())
            task = next(task for task in tasks if task.get('register') == variable)
            yield Environment().compile_expression(task['failed_when']), variable

    def test_present_container_is_not_an_inspect_failure(self):
        for predicate, variable in self.conditions():
            self.assertFalse(predicate(**{variable: {'rc': 0, 'stderr': ''}}))

    def test_exact_absence_accepts_old_and_current_cli_case(self):
        for predicate, variable in self.conditions():
            for message in ['error: no such object: web-saas-postgresql',
                            'Error: No such object: web-saas-postgresql\n',
                            'Error response from daemon: No such container: web-saas-postgresql']:
                self.assertFalse(predicate(**{variable: {'rc': 1, 'stderr': message}}))

    def test_unknown_errors_wrong_target_or_status_are_refused(self):
        for predicate, variable in self.conditions():
            for rc, message in [(1, 'permission denied'), (1, 'Cannot connect to the Docker daemon'),
                    (1, 'error: no such object: different-container'),
                    (125, 'error: no such object: web-saas-postgresql'),
                    (1, 'error: no such object: web-saas-postgresql\npermission denied')]:
                self.assertTrue(predicate(**{variable: {'rc': rc, 'stderr': message}}))

    def test_full_container_inspect_does_not_log_runtime_environment(self):
        tasks = yaml.safe_load((ROOT / 'roles/web_saas_prod_storage/tasks/main.yml').read_text())
        task = next(task for task in tasks if task.get('register') == 'web_saas_prod_storage_postgres')
        self.assertTrue(task['no_log'])


if __name__ == '__main__':
    unittest.main()
