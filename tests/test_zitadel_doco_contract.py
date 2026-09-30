"""Source-level ownership and data-preservation checks; not a live deployment."""
from pathlib import Path
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]


class ZitadelDocoContract(unittest.TestCase):
    def test_entrypoint_and_legacy_are_mutually_exclusive(self):
        play = yaml.safe_load((ROOT / 'deploy_zitadel_docker.yaml').read_text())[0]
        self.assertEqual(play['vars']['zitadel_deployment_mode'], 'doco-cd')
        tasks = yaml.safe_load((ROOT / 'roles/docker/zitadel/tasks/main.yml').read_text())
        includes = [task for task in tasks if 'ansible.builtin.include_tasks' in task]
        self.assertEqual([t['when'] for t in includes],
                         ["zitadel_deployment_mode == 'doco-cd'", "zitadel_deployment_mode == 'compose'"])

    def test_gitops_target_and_digest_health_not_just_reconciler_health(self):
        tasks = yaml.safe_load((ROOT / 'roles/docker/zitadel/tasks/doco-cd.yml').read_text())
        delegate = next(t for t in tasks if 'ansible.builtin.include_role' in t)
        poll = delegate['vars']['doco_cd_poll_config'][0]
        self.assertEqual(poll['target'], 'zitadel')
        self.assertEqual(poll['reference'], '{{ zitadel_gitops_revision }}')
        health = next(t for t in tasks if t.get('until'))
        self.assertIn("item.image ~ ' healthy'", health['until'])
        self.assertEqual(len(health['loop']), 2)

    def test_no_direct_stack_start_reset_or_secret_logging(self):
        tasks = yaml.safe_load((ROOT / 'roles/docker/zitadel/tasks/doco-cd.yml').read_text())
        text = (ROOT / 'roles/docker/zitadel/tasks/doco-cd.yml').read_text()
        for forbidden in ['volume rm', 'down --volumes', 'run --rm', '|| true', 'docker compose']:
            self.assertNotIn(forbidden, text)
        for task in tasks:
            if 'ansible.builtin.template' in task and '/etc/xcontrol/zitadel/' in task['ansible.builtin.template']['dest']:
                self.assertTrue(task['no_log'])
                self.assertEqual(task['ansible.builtin.template']['mode'], '0600')
        self.assertIn('Reject accidental masterkey rotation', text)
        self.assertIn('Legacy Compose found', text)


if __name__ == '__main__':
    unittest.main()
