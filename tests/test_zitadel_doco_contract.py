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
        gate = next(t for t in tasks if t.get('name') == 'Wait for Doco-CD to reconcile the ZITADEL stack')
        health = next(t for t in gate['block'] if t.get('until'))
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

    def test_failed_reconcile_reports_doco_cd_log_and_stack_state(self):
        tasks = yaml.safe_load((ROOT / 'roles/docker/zitadel/tasks/doco-cd.yml').read_text())
        gate = next(t for t in tasks if t.get('name') == 'Wait for Doco-CD to reconcile the ZITADEL stack')
        rescue = gate['rescue']
        log = next(t for t in rescue if t['name'] == 'Read the Doco-CD reconciliation log')
        self.assertEqual(log['ansible.builtin.command']['argv'][:2], ['docker', 'logs'])
        self.assertEqual(log['ansible.builtin.command']['argv'][-1], 'doco-cd-zitadel')
        state = next(t for t in rescue if t['name'] == 'Read the ZITADEL stack container state')
        self.assertIn('label=com.docker.compose.project=shared-zitadel', state['ansible.builtin.command']['argv'])
        # Both the API and the Login service report probe output and logs.
        health = next(t for t in rescue if t['name'] == 'Read the ZITADEL service health probe results')
        self.assertEqual(health['ansible.builtin.command']['argv'][-1], 'shared-zitadel-{{ item }}-1')
        self.assertEqual(health['loop'], ['zitadel', 'login'])
        service_logs = next(t for t in rescue if t['name'] == 'Read the ZITADEL service logs')
        self.assertEqual(service_logs['ansible.builtin.command']['argv'][:2], ['docker', 'logs'])
        self.assertEqual(service_logs['ansible.builtin.command']['argv'][-1], 'shared-zitadel-{{ item }}-1')
        self.assertEqual(service_logs['loop'], ['zitadel', 'login'])
        # Evidence reads are read-only and never mask the original failure.
        for task in rescue[:-1]:
            if 'ansible.builtin.stat' in task:
                continue
            self.assertEqual(task['failed_when'], False)
            self.assertEqual(task['changed_when'], False)
            self.assertIn(task['ansible.builtin.command']['argv'][1], ('logs', 'ps', 'inspect'))
        # Diagnostics must never turn a failed deploy green.
        self.assertIn('ansible.builtin.fail', rescue[-1])

    def test_login_env_is_literal_dotenv_for_doco_cd(self):
        # compose-go (Doco-CD) rejects env_file format "raw"; the file must be
        # dotenv whose single-quoted values survive $, #, spaces and quotes.
        import re
        from jinja2 import Environment
        # trim_blocks matches the ansible.builtin.template default.
        template = Environment(trim_blocks=True).from_string(
            (ROOT / 'roles/docker/zitadel/templates/login.env.j2').read_text())
        secret = 'a$b{c}#d "e" f\\g' + 'x' * 32
        rendered = template.render(zitadel_domain='iam.svc.plus', zitadel_login_session_cookie_secret=secret)
        values = {}
        for line in rendered.splitlines():
            match = re.fullmatch(r"([A-Z_]+)='([^']*)'", line)
            self.assertIsNotNone(match, line)
            values[match.group(1)] = match.group(2)
        self.assertEqual(values['ZITADEL_SESSION_COOKIE_SECRET'], secret)
        self.assertEqual(values['CUSTOM_REQUEST_HEADERS'], 'Host:iam.svc.plus,X-Forwarded-Proto:https')
        guard = (ROOT / 'roles/docker/zitadel/tasks/doco-cd.yml').read_text()
        self.assertIn('"\\"\'\\" not in zitadel_login_session_cookie_secret"', guard)

    def test_unbootstrapped_reset_is_explicit_backed_up_and_scoped(self):
        tasks = yaml.safe_load((ROOT / 'roles/docker/zitadel/tasks/doco-cd.yml').read_text())
        names = [task.get('name') for task in tasks]
        reset = tasks[names.index('Rebuild the ZITADEL database of an unbootstrapped instance')]
        self.assertEqual(reset['ansible.builtin.include_tasks'], 'reset-unbootstrapped.yml')
        self.assertEqual(reset['when'], 'zitadel_reset_unbootstrapped_instance | bool')
        # The database is rebuilt before Doco-CD starts the stack again.
        self.assertLess(names.index('Rebuild the ZITADEL database of an unbootstrapped instance'),
                        names.index('Install independently scoped IAM GitOps reconciler'))
        defaults = yaml.safe_load((ROOT / 'roles/docker/zitadel/defaults/main.yml').read_text())
        self.assertIs(defaults['zitadel_reset_unbootstrapped_instance'], False)
        self.assertEqual(defaults['zitadel_reset_confirmation'], '')

        path = ROOT / 'roles/docker/zitadel/tasks/reset-unbootstrapped.yml'
        steps = yaml.safe_load(path.read_text())
        order = [step['name'] for step in steps]
        self.assertIn("== 'RESET-ZITADEL-DATABASE'", steps[0]['ansible.builtin.assert']['that'])
        refuse = order.index('Refuse to reset a completed bootstrap')
        self.assertEqual(steps[refuse]['ansible.builtin.assert']['that'], 'not zitadel_reset_login_pat.stat.exists')
        sequence = [order.index(name) for name in (
            'Refuse to reset a completed bootstrap',
            'Pause the IAM reconciler and the ZITADEL services',
            'Back up the ZITADEL database before the reset',
            'Verify the backup is a readable custom-format dump',
            'Drop only the ZITADEL database',
            'Recreate the empty ZITADEL database as the normal flow does',
        )]
        self.assertEqual(sequence, sorted(sequence))
        drop = steps[order.index('Drop only the ZITADEL database')]['ansible.builtin.command']['argv']
        self.assertEqual(drop[-1], 'DROP DATABASE {{ zitadel_db_name }} WITH (FORCE);')
        create = steps[order.index('Recreate the empty ZITADEL database as the normal flow does')]['ansible.builtin.command']['argv']
        self.assertEqual(create[-1], "CREATE DATABASE {{ zitadel_db_name }} OWNER {{ zitadel_db_user }} ENCODING 'UTF8';")
        text = '\n'.join(line for line in path.read_text().splitlines() if not line.lstrip().startswith('#'))
        for forbidden in ('volume', 'down', 'rm ', '|| true', 'docker compose', 'data_dir', '/opt/open-platform'):
            self.assertNotIn(forbidden, text)

    def test_first_instance_steps_create_admin_and_login_client(self):
        # start-from-init reads FirstInstance through --steps; GitOps passes
        # this rendered file for both --config and --steps.
        import json
        from jinja2 import Environment
        env = Environment(trim_blocks=True)
        env.filters['to_json'] = json.dumps
        env.globals['lookup'] = lambda plugin, name: 'env-' + name  # Ansible lookup shim
        template = env.from_string((ROOT / 'roles/docker/zitadel/templates/config.yaml.j2').read_text())
        values = dict(zitadel_domain='iam.svc.plus', zitadel_db_host='postgresql-svc-plus', zitadel_db_port=5432,
                      zitadel_db_name='zitadel', zitadel_db_user='zitadel_user', zitadel_db_password='p1',
                      zitadel_db_admin_user='postgres', zitadel_db_admin_password='p2',
                      zitadel_admin_password_override='Adm1n!pass', zitadel_admin_password='Adm1n!pass')
        first = yaml.safe_load(template.render(**values))['FirstInstance']
        human = first['Org']['Human']
        self.assertEqual(human['UserName'], 'zitadel-admin@iam.svc.plus')
        self.assertEqual(human['Email'], {'Address': 'zitadel-admin@iam.svc.plus', 'Verified': True})
        self.assertIs(human['PasswordChangeRequired'], False)
        self.assertNotEqual(human['Password'], 'Password1!')
        self.assertEqual(first['Org']['LoginClient']['Machine']['Username'], 'login-client')
        self.assertEqual(first['LoginClientPatPath'], '/current-dir/login-client.pat')

    def test_caddy_reloads_the_validated_entry_before_the_stack_wait(self):
        # A handler is lost when the play fails before flushing; the reload must
        # not depend on it, and must follow validation and precede the wait.
        tasks = yaml.safe_load((ROOT / 'roles/docker/zitadel/tasks/doco-cd.yml').read_text())
        names = [task.get('name') for task in tasks]
        apply = tasks[names.index('Apply validated Caddy entry')]
        self.assertEqual(apply['ansible.builtin.service'], {'name': 'caddy', 'state': 'reloaded'})
        self.assertNotIn('when', apply)
        self.assertLess(names.index('Validate Caddy entry before continuing'), names.index('Apply validated Caddy entry'))
        self.assertLess(names.index('Apply validated Caddy entry'), names.index('Install independently scoped IAM GitOps reconciler'))
        self.assertLess(names.index('Apply validated Caddy entry'), names.index('Wait for Doco-CD to reconcile the ZITADEL stack'))

if __name__ == '__main__':
    unittest.main()
