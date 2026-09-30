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


    def test_doco_cd_never_mounts_a_placeholder_docker_config(self):
        # Doco-CD exits at startup when its docker config path is not a regular
        # file, so a Docker-created placeholder directory stops all reconciling.
        tasks = yaml.safe_load((ROOT / 'roles/vhosts/Doco-CD/tasks/main.yml').read_text())
        by_name = {task['name']: task for task in tasks}
        cleanup = by_name['Remove the placeholder directory Docker creates for a missing config.json']
        self.assertEqual(cleanup['ansible.builtin.command']['argv'], ['rmdir', '/root/.docker/config.json'])
        self.assertEqual(cleanup['when'], 'doco_cd_docker_config_before.stat.isdir | default(false)')
        self.assertNotIn('when', by_name['Assert the registry credentials landed as a file'])
        mount = by_name['Mount the host registry config only when it is a regular file']
        self.assertIn('stat.isreg', mount['ansible.builtin.set_fact']['doco_cd_mount_docker_config'])

        import json
        import shlex
        from jinja2 import Environment
        env = Environment()
        # Ansible filters used by the template.
        env.filters.update(quote=shlex.quote, to_json=json.dumps,
                           bool=lambda value: str(value).strip().lower() in ('1', 'true', 'yes', 'on'))
        template = env.from_string((ROOT / 'roles/vhosts/Doco-CD/templates/docker-compose.yml.j2').read_text())
        base = dict(doco_cd_container_name='doco-cd-zitadel', doco_cd_image='img', doco_cd_webhook_port='1',
                    doco_cd_metrics_port='2', doco_cd_timezone='UTC', doco_cd_git_access_token='',
                    doco_cd_enable_webhook=False, doco_cd_poll_config=[])
        for mounted in (False, True):
            rendered = yaml.safe_load(template.render(**base, doco_cd_mount_docker_config=mounted))
            volumes = rendered['services']['app']['volumes']
            self.assertEqual('/root/.docker/config.json:/root/.docker/config.json:ro' in volumes, mounted)

    def test_doco_cd_must_be_healthy_before_stacks_are_awaited(self):
        tasks = yaml.safe_load((ROOT / 'roles/vhosts/Doco-CD/tasks/main.yml').read_text())
        names = [task['name'] for task in tasks]
        self.assertLess(names.index('Start Doco-CD'), names.index('Wait for Doco-CD to become healthy'))
        gate = tasks[names.index('Wait for Doco-CD to become healthy')]
        self.assertIn("== 'healthy'", gate['block'][0]['until'])
        self.assertTrue(any('ansible.builtin.fail' in task for task in gate['rescue']))

if __name__ == '__main__':
    unittest.main()
