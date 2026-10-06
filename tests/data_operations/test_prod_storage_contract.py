"""Source ownership and exact storage/environment integration; no live mutation."""
from pathlib import Path
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[2]


class ProdStorageContractTests(unittest.TestCase):
    def test_storage_precedes_secrets_and_reconciler(self):
        play = yaml.safe_load((ROOT/'setup-web-saas-domain.yml').read_text())[0]
        self.assertEqual(play['roles'][0]['role'], 'roles/web_saas_prod_storage')
        self.assertIn("== 'prod'", play['roles'][0]['when'])
        poll = play['vars']['doco_cd_poll_config'][0]
        self.assertIn("'prod'", poll['target'])
        self.assertIn("else ''", poll['target'])

    def test_exact_cmdb_contract_and_no_infrastructure_operations(self):
        text = (ROOT/'roles/web_saas_prod_storage/tasks/main.yml').read_text()
        for expected in ('projects/open-platform-prod/zones/asia-east1-a/disks/web-saas-prod-data',
                         "device_name == 'web-saas-prod-data'", "mount_path == '/data'",
                         "'Source', 'equalto', '/data/postgresql'", 'roles/web_saas_data_volume'):
            self.assertIn(expected, text)
        for forbidden in ('gcloud', 'terraform', 'uri:', 'mkfs', 'force: true'):
            self.assertNotIn(forbidden, text)
        self.assertLess(text.index('Refuse existing PostgreSQL'), text.index('Mount the declared'))

    def test_stop_guard_init_resume_order(self):
        text = (ROOT/'.github/workflows/selfhost-database-operations.yml').read_text()
        stop, guard, init = [text.index(value) for value in (
            'application-state.sh stop', 'init_guard.sh', 'ansible-playbook -i')]
        self.assertLess(stop, guard)
        self.assertLess(guard, init)
        self.assertIn("steps.pause.outcome == 'success'", text)

    def test_query_failure_cannot_default_to_empty(self):
        text = (ROOT/'initialize-web-saas-schemas.yml').read_text()
        self.assertIn('public_table_count_check.rc == 0', text)
        task = [task for task in yaml.safe_load(text)[0]['tasks']
                if task.get('register') == 'public_table_count_check'][0]
        self.assertNotIn('failed_when', task)


if __name__ == '__main__':
    unittest.main()
