"""Local owner contract/runner/role fixtures. Never deployment acceptance."""
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import uuid

import yaml

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/control_plane'))
import entrypoint as owner
import migration
import runtime


def imported(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


native = imported('native_init_host', ROOT / 'scripts/data_operations/selfhost/native_init_host.py')
sys.modules['native_init_host'] = native
business = imported('business_database', ROOT / 'roles/web_saas_business_database/files/business_database.py')
sys.modules['business_database'] = business
native_runtime = imported('native_runtime', ROOT / 'roles/selfhost_control_plane/files/native_runtime.py')
readiness = imported('candidate_readiness', ROOT / 'roles/selfhost_control_plane/files/candidate_readiness.py')
adapter = imported('api_backup_receipt', ROOT / 'roles/selfhost_core_data_backup/files/api_backup_receipt.py')


def utc(delta=3600):
    return (datetime.now(timezone.utc) + timedelta(seconds=delta)).isoformat().replace('+00:00', 'Z')


def policy():
    refs = {key: 'kv/uat/control-plane/runtime#' + key for key in (*owner.SECRET_KEYS, *owner.RUNTIME_KEYS)}
    refs.update(POSTGRES_PASSWORD='kv/uat/databases#postgres_root_password',
                ACCOUNT_PG_PASSWORD='kv/uat/databases#account_pg_password',
                INTERNAL_SERVICE_TOKEN='kv/uat/accounts/runtime#INTERNAL_SERVICE_TOKEN')
    return {'vault_addr': 'https://vault.example.invalid', 'vault_token_env': 'VAULT_TOKEN', 'secret_refs': refs}


def migration_policy():
    return {'schema_version': 1, 'kind': 'accounts-migration', 'environment': 'uat',
            'source': {'url': 'https://source.example.invalid', 'allowed_target_urls': ['https://target.example.invalid'],
                       'auth': {'token_env': 'SOURCE_MIGRATION_TOKEN', 'scopes': ['source.read'], 'expires_at': utc()}},
            'target': {'url': 'https://target.example.invalid', 'allowed_source_urls': ['https://source.example.invalid'],
                       'auth': {'token_env': 'TARGET_MIGRATION_TOKEN', 'scopes': ['target.plan', 'target.apply'], 'expires_at': utc()}}}


class RepositoryFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='control-plane-unit-')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name).resolve()
        self.owner = self.directory / 'playbooks'
        self.gitops = self.directory / 'gitops'
        for root in (self.owner, self.gitops):
            root.mkdir()
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            self.git(root, 'config', 'user.name', 'Owner fixture')
            self.git(root, 'config', 'user.email', 'fixture@invalid.example')
        (self.owner / 'inventory').mkdir()
        shutil.copy(ROOT / 'inventory/terraform_cmdb.py', self.owner / 'inventory/terraform_cmdb.py')
        shutil.copytree(ROOT / 'scripts/control_plane', self.owner / 'scripts/control_plane', ignore=shutil.ignore_patterns('__pycache__'))
        self.commit(self.owner)
        self.owner_sha = self.git(self.owner, 'rev-parse', 'HEAD')
        self.images = {k: 'ghcr.io/example/' + k + ':RC1@sha256:' + str(i) * 64 for i, k in enumerate(owner.SERVICES, 1)}
        self.contract = {'schema_version': 1, 'kind': 'selfhost-control-plane', 'environment': 'uat',
            'target': {'host': 'arbitrary-target.example.invalid', 'generation': 'vnext', 'domain_base': 'example.invalid',
                       'console_domain': 'console-candidate.example.invalid', 'accounts_domain': 'identity.example.invalid'},
            'release': {'snapshot_tag': 'uat-daily-build-2026.10.23-r101', 'services': self.images,
                'accounts': {'source_sha': 'a' * 40, 'schema_sha256': 'b' * 64, 'billing_schema_sha256': 'c' * 64,
                             'migration_version': 2026102002, 'business_tables': ['users']},
                'doco_cd': {'image': 'ghcr.io/kimdre/doco-cd@sha256:' + 'd' * 64,
                    'repository': 'https://github.com/ai-workspace-infra/gitops.git', 'target': 'candidate', 'storage_target': 'storage'}},
            'execution': {'playbooks_sha': self.owner_sha, 'mode': 'candidate', 'initialize': True}}
        postgres = {'image': self.images['postgresql'], 'env_file': ['/etc/xcontrol/web-saas/secrets.env'], 'volumes': [{'type': 'bind', 'source': '/data/postgresql',
                    'target': '/var/lib/postgresql/data', 'bind': {'create_host_path': False}}]}
        services = {service: {'image': self.images[key], 'env_file': ['/etc/xcontrol/web-saas/secrets.env']} for key, service in owner.SERVICES.items()}
        services['postgres'] = postgres
        for name in ('accounts', 'billing'):
            services[name]['environment'] = {'DATABASE_RUNTIME_ROLE': 'primary', 'DATABASE_BACKGROUND_WRITERS': 'false'}
        (self.gitops / 'compose').mkdir()
        self.write(self.gitops / 'compose/storage.yml', {'services': {'postgres': postgres}})
        self.write(self.gitops / 'compose/application.yml', {'services': services})
        for selection, file in (('storage', 'storage.yml'), ('candidate', 'application.yml')):
            self.write(self.gitops / ('.doco-cd.' + selection + '.yml'), {'name': 'fixture', 'version': 'doco.v1',
                'working_dir': 'compose', 'compose_files': [file], 'env_files': [], 'remove_orphans': False})
        self.write(self.gitops / 'candidate.yaml', self.contract)
        self.commit(self.gitops)
        self.sha = self.git(self.gitops, 'rev-parse', 'HEAD')
        # Real legacy Akamai shape: no top-level environment; numeric instance ID.
        self.cmdb = {self.contract['target']['host']: {'ip': '192.0.2.91', 'instance_id': 123, 'ansible_user': 'root',
            'groups': ['web_saas', 'uat'], 'storage': {'profile': 'root', 'data_path': '/data/postgresql'}}}
        self.cmdb_path = self.directory / 'cmdb.json'
        self.cmdb_path.write_text(json.dumps(self.cmdb))
        self.vars_path = self.directory / 'policy.yaml'
        self.write(self.vars_path, {'control_plane_runtime': policy()})
        self.vars_path.chmod(0o600)
        self.env = {'PLAYBOOKS_CHECKOUT': str(self.owner), 'GITOPS_CHECKOUT': str(self.gitops),
                    'CONTROL_PLANE_CMDB': str(self.cmdb_path), 'CONTROL_PLANE_VARS_FILE': str(self.vars_path)}
        self.patch = mock.patch.dict(os.environ, self.env)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    @staticmethod
    def write(path, value):
        path.write_text(yaml.safe_dump(value, sort_keys=False))

    @staticmethod
    def git(root, *args):
        return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()

    def commit(self, root):
        self.git(root, 'add', '.')
        self.git(root, 'commit', '-qm', 'Isolated fixture')

    def changed(self):
        self.write(self.gitops / 'candidate.yaml', self.contract)
        self.commit(self.gitops)
        self.sha = self.git(self.gitops, 'rev-parse', 'HEAD')

    def load(self):
        return owner.load_contract('candidate.yaml', self.gitops, self.owner, self.sha)

    def test_plan_is_readonly_without_gitops_snapshot_tag_or_vault_identity(self):
        before = sorted(str(p.relative_to(self.owner)) for p in self.owner.rglob('*'))
        result = subprocess.run([sys.executable, str(self.owner / 'scripts/control_plane/entrypoint.py'),
            '--config', 'candidate.yaml', '--gitops-ref', self.sha, '--check'], env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'},
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)['host_actions'])
        self.assertEqual(self.git(self.gitops, 'tag'), '')
        self.assertEqual(before, sorted(str(p.relative_to(self.owner)) for p in self.owner.rglob('*')))
        self.assertEqual(self.git(self.owner, 'status', '--porcelain', '--ignored'), '')

    def test_opt_in_public_installer_to_actual_pinned_owner_check(self):
        installer = os.environ.get('CONTROL_PLANE_PUBLIC_INSTALLER')
        if not installer:
            self.skipTest('Set CONTROL_PLANE_PUBLIC_INSTALLER to the reviewed public wrapper')
        self.git(self.owner, 'remote', 'add', 'origin', 'https://github.com/ai-workspace-infra/playbooks.git')
        self.git(self.gitops, 'remote', 'add', 'origin', 'https://github.com/ai-workspace-infra/gitops.git')
        env = {**os.environ, 'CONTROL_PLANE_OFFLINE_TEST': '1', 'PYTHONDONTWRITEBYTECODE': '1',
               'PATH': str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH']}
        result = subprocess.run(['bash', str(Path(installer).resolve(strict=True)), '--config', 'candidate.yaml',
            '--gitops-ref', self.sha, '--check'], env=env, cwd=self.directory, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)['host_actions'])
        for checkout in (self.owner, self.gitops):
            self.assertEqual(self.git(checkout, 'status', '--porcelain', '--ignored'), '')

    def test_rejects_wrong_gitops_sha_and_dirty_owner(self):
        with self.assertRaises(owner.Refused):
            owner.load_contract('candidate.yaml', self.gitops, self.owner, '0' * 40)
        (self.owner / 'unreviewed').write_text('fixture')
        with self.assertRaisesRegex(owner.Refused, 'clean'):
            self.load()

    def test_rejects_config_escape_and_symlink_escape(self):
        with self.assertRaises(owner.Refused):
            owner.inside(self.gitops, self.gitops, '../cmdb.json')
        (self.gitops / 'escape.yaml').symlink_to(self.vars_path)
        with self.assertRaises(owner.Refused):
            owner.inside(self.gitops, self.gitops, 'escape.yaml')

    def test_rejects_unknown_legacy_file_inputs_and_example(self):
        for mutate in (lambda c: c.update(vars_files=[]), lambda c: c['target'].update(cmdb_file='x'),
                       lambda c: c.update(example_only=True)):
            original = copy.deepcopy(self.contract)
            mutate(self.contract)
            self.changed()
            with self.assertRaises(owner.Refused):
                self.load()
            self.contract = original

    def test_first_sit_plan_preserves_explicit_environment_without_uat_fallback(self):
        self.contract['environment'] = 'sit'
        self.changed()
        self.cmdb[self.contract['target']['host']]['groups'] = ['sit']
        self.cmdb_path.write_text(json.dumps(self.cmdb))
        value = policy()
        value['secret_refs'] = {key: reference.replace('kv/uat/', 'kv/sit/') for key, reference in value['secret_refs'].items()}
        self.write(self.vars_path, {'control_plane_runtime': value})
        self.assertEqual(self.load()[0]['environment'], 'sit')

    def test_accepts_declared_root_storage_with_actual_legacy_cmdb(self):
        del self.cmdb[self.contract['target']['host']]['storage']
        self.cmdb_path.write_text(json.dumps(self.cmdb))
        self.contract['execution']['storage'] = {'profile': 'root', 'data_path': '/data/postgresql'}
        self.changed()
        self.assertEqual(self.load()[0]['resolved']['data_disk']['profile'], 'root')

    def test_rejects_cmdb_connection_override_missing_host_and_wrong_env(self):
        record = self.cmdb[self.contract['target']['host']]
        for field, value in (('host_vars', {'ansible_connection': 'local'}), ('groups', ['prod'])):
            original = copy.deepcopy(record)
            record[field] = value
            self.cmdb_path.write_text(json.dumps(self.cmdb))
            with self.assertRaises(owner.Refused):
                self.load()
            record.clear()
            record.update(original)

    def test_rejects_moving_images_writer_enable_and_compose_escape(self):
        path = self.gitops / 'compose/application.yml'
        original = owner.read_yaml(path)
        for change in (lambda d: d['services']['accounts'].update(image='ghcr.io/example/accounts:latest'),
                       lambda d: d['services']['billing']['environment'].update(DATABASE_BACKGROUND_WRITERS='true'),
                       lambda d: d['services']['accounts'].update(build='.'),
                       lambda d: d['services']['accounts'].update(extends={'file': '../../escape', 'service': 'accounts'})):
            value = copy.deepcopy(original)
            change(value)
            self.write(path, value)
            self.commit(self.gitops)
            self.sha = self.git(self.gitops, 'rev-parse', 'HEAD')
            with self.assertRaises((owner.Refused, OSError)):
                self.load()

    def test_runner_calls_only_exact_cmdb_inventory_target_and_stops_on_failure(self):
        contract, refs = self.load()
        receipt = 'CONTROL_PLANE_EVIDENCE=' + json.dumps({'status': 'candidate_runtime_ready', 'business_acceptance': False})
        with mock.patch.object(owner.subprocess, 'run', return_value=mock.Mock(returncode=0, stdout=receipt)) as execute:
            output = owner.run_owner(self.owner, contract, refs)
        self.assertTrue(output['host_actions'])
        self.assertEqual(len(execute.call_args_list), 4)
        for call in execute.call_args_list:
            self.assertIn(str(self.owner / 'inventory/terraform_cmdb.py'), call.args[0])
            self.assertIn(contract['target']['host'], call.args[0])
            self.assertNotIn('secrets', call.kwargs.get('input') or '')
            self.assertEqual(call.kwargs['env']['ANSIBLE_CONFIG'], str(self.owner / 'scripts/control_plane/ansible.cfg'))
        with mock.patch.object(owner.subprocess, 'run', return_value=mock.Mock(returncode=1)) as execute:
            with self.assertRaises(owner.Refused):
                owner.run_owner(self.owner, contract, refs)
            self.assertEqual(execute.call_count, 1)

    def test_success_without_actual_readiness_receipt_is_refused(self):
        contract, refs = self.load()
        with mock.patch.object(owner.subprocess, 'run', return_value=mock.Mock(returncode=0, stdout='no hosts matched')):
            with self.assertRaisesRegex(owner.Refused, 'readiness'):
                owner.run_owner(self.owner, contract, refs)

    def test_actual_gitops_schema_sample_and_uppercase_tag(self):
        schema_path = os.environ.get('CONTROL_PLANE_GITOPS_SCHEMA')
        if not schema_path:
            self.skipTest('Set CONTROL_PLANE_GITOPS_SCHEMA to the reviewed companion schema')
        import jsonschema
        jsonschema.Draft202012Validator(owner.read_yaml(Path(schema_path))).validate(self.contract)
        self.load()


class RuntimeTests(unittest.TestCase):
    def test_custom_vault_identity_overrides_stale_default_and_agent(self):
        declaration = policy()
        declaration['vault_token_env'] = 'JOB_VAULT_TOKEN'
        records = {}
        for key, reference in declaration['secret_refs'].items():
            path, field = reference.split('#')
            records.setdefault(path, {})[field] = 'fixture-material'
        def run(argv, **kwargs):
            self.assertEqual(kwargs['env']['VAULT_TOKEN'], 'selected-job-identity')
            self.assertNotIn('VAULT_AGENT_ADDR', kwargs['env'])
            return mock.Mock(returncode=0, stdout=json.dumps({'data': {'data': records[argv[-1]]}}))
        with mock.patch.dict(os.environ, {'VAULT_TOKEN': 'stale-default', 'JOB_VAULT_TOKEN': 'selected-job-identity',
                                          'VAULT_AGENT_ADDR': 'http://stale-agent'}), mock.patch.object(runtime.subprocess, 'run', side_effect=run):
            runtime.resolve(declaration, 'uat')
        with mock.patch.dict(os.environ, {'VAULT_TOKEN': 'stale-default'}, clear=True), mock.patch.object(runtime.subprocess, 'run') as execute:
            with self.assertRaises(owner.Refused):
                runtime.resolve(declaration, 'uat')
            execute.assert_not_called()

    def test_vault_resolution_is_memory_only_canonical_and_no_secret_arguments(self):
        declaration = policy()
        records = {}
        for key, reference in declaration['secret_refs'].items():
            path, field = reference.split('#')
            records.setdefault(path, {})[field] = 'fixture-password-or-material'
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            return mock.Mock(returncode=0, stdout=json.dumps({'data': {'data': records[argv[-1]]}}))
        with mock.patch.dict(os.environ, {'VAULT_TOKEN': 'fixture-vault-token'}), mock.patch.object(runtime.subprocess, 'run', side_effect=run):
            resolved = runtime.resolve(declaration, 'uat', {'accounts_domain': 'identity.example.invalid',
                'console_domain': 'ui.example.invalid', 'domain_base': 'example.invalid'})
        self.assertEqual(resolved['secrets']['ACCOUNT_PG_USER'], 'account_user')
        self.assertEqual(resolved['secrets']['DATABASE_BACKGROUND_WRITERS'], 'false')
        self.assertEqual(resolved['secrets']['STRIPE_SECRET_KEY'], '')
        self.assertEqual(resolved['secrets']['NEXT_PUBLIC_ACCOUNT_SERVICE_URL'], 'https://identity.example.invalid')
        self.assertEqual(resolved['secrets']['NEXT_PUBLIC_SERVER_SERVICE_URL'], 'https://ui.example.invalid/api')
        self.assertEqual(resolved['secrets']['XWORKMATE_SHARED_TENANT_DOMAIN'], 'example.invalid')
        self.assertIn('@stunnel-client:15432/account?sslmode=disable', resolved['secrets']['DATABASE_URL'])
        self.assertTrue(all('fixture-password' not in str(c) and 'fixture-vault-token' not in str(c) for c in calls))
        self.assertEqual(len(calls), len(records))

    def test_empty_wrong_environment_or_noncanonical_refs_refused(self):
        for key, reference in (('ACCOUNT_PG_PASSWORD', 'kv/uat/other#password'),
                               ('INTERNAL_SERVICE_TOKEN', 'kv/prod/accounts/runtime#INTERNAL_SERVICE_TOKEN')):
            value = policy()
            value['secret_refs'][key] = reference
            with self.assertRaises(owner.Refused):
                runtime.resolve(value, 'uat')
        with mock.patch.dict(os.environ, {'VAULT_TOKEN': 'fixture'}), mock.patch.object(runtime.subprocess, 'run',
                return_value=mock.Mock(returncode=0, stdout='{"data":{"data":{}}}')):
            with self.assertRaises(owner.Refused):
                runtime.resolve(policy(), 'uat')


class ReadinessTests(unittest.TestCase):
    def test_trusted_sni_probe_never_disables_tls_or_follows_redirects(self):
        with mock.patch.object(readiness.subprocess, 'run', return_value=mock.Mock(returncode=0, stdout='200')) as execute:
            readiness.sni_probe('candidate.example.invalid', '/readyz')
        command = execute.call_args.args[0]
        self.assertNotIn('-k', command)
        self.assertNotIn('--insecure', command)
        self.assertIn('--resolve', command)
        self.assertNotIn('--location', command)
        with mock.patch.object(readiness.subprocess, 'run', return_value=mock.Mock(returncode=60, stdout='')):
            with self.assertRaises(readiness.Refused):
                readiness.sni_probe('candidate.example.invalid', '/')

    def test_actual_http_body_and_database_readiness_are_required(self):
        from http.server import BaseHTTPRequestHandler, HTTPServer
        import threading
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"status":"ready","database":"ok"}')
            def log_message(self, *_):
                pass
        server = HTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            readiness.http_probe('127.0.0.1', server.server_port, '/readyz', 'fixture', {'status': 'ready', 'database': 'ok'})
            with self.assertRaises(readiness.Refused):
                readiness.http_probe('127.0.0.1', server.server_port, '/', 'fixture', {}, html=True)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


class NativeRoleTests(unittest.TestCase):
    def test_postgres_final_probe_rejects_temporary_server_before_readiness(self):
        probe = (ROOT / 'roles/selfhost_control_plane/files/postgres_final_ready.sh').read_text()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / 'calls'
            for command, body in {
                'cat': 'echo "$FIXTURE_PID1"',
                'pg_isready': 'echo ready >> "$FIXTURE_CALLS"',
                'psql': 'echo sql >> "$FIXTURE_CALLS"; echo 1',
            }.items():
                path = root / command
                path.write_text('#!/bin/sh\n' + body + '\n')
                path.chmod(0o700)
            env = {**os.environ, 'PATH': str(root) + os.pathsep + os.environ['PATH'],
                   'FIXTURE_CALLS': str(marker), 'FIXTURE_PID1': 'docker-entrypoi'}
            result = subprocess.run(['sh'], input=probe, text=True, env=env, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(marker.exists())
            env['FIXTURE_PID1'] = 'postgres'
            result = subprocess.run(['sh'], input=probe, text=True, env=env, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(marker.read_text().splitlines(), ['ready', 'sql'])

    def test_native_caddy_template_uses_declared_aliases_and_readonly_candidate_route(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            destination = root / 'Caddyfile'
            fixture = root / 'render.yml'
            fixture.write_text(yaml.safe_dump([{'hosts': 'localhost', 'connection': 'local', 'gather_facts': False,
                'vars_files': [str(ROOT / 'roles/vhosts/web_saas_host_config/defaults/main.yml')],
                'tasks': [{'ansible.builtin.template': {'src': str(ROOT / 'roles/vhosts/web_saas_host_config/templates/Caddyfile.j2'),
                                                       'dest': str(destination)}}]}]))
            variables = {'web_saas_host_config_candidate_mode': True, 'web_saas_host_config_use_restored_tls': True,
                'web_saas_host_config_acme_dns_enabled': False, 'web_saas_host_config_target_domain_base': 'example.invalid',
                'web_saas_host_config_console_domain': 'arbitrary-ui.example.invalid',
                'web_saas_host_config_console_public_domain': 'arbitrary-ui.example.invalid',
                'web_saas_host_config_accounts_domain': 'identity.example.invalid',
                'web_saas_host_config_accounts_public_domain': 'identity.example.invalid',
                'web_saas_host_config_deploy_env': 'sit', 'web_saas_host_config_billing_domain': '',
                'web_saas_host_config_brand_domain': '', 'web_saas_host_config_brand_selfhost_domain': ''}
            result = subprocess.run(['ansible-playbook', '-i', 'localhost,', str(fixture), '-e', json.dumps(variables)],
                env={**os.environ, 'ANSIBLE_CONFIG': str(ROOT / 'scripts/control_plane/ansible.cfg')}, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            content = destination.read_text()
            self.assertIn('arbitrary-ui.example.invalid, identity.example.invalid {', content)
            self.assertIn('candidate ingress is read-only', content)
            self.assertNotIn('dns cloudflare', content)

    def test_real_ansible_selection_rerun_has_zero_changes_and_no_extra_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            compose = root / 'compose.yml'
            compose.write_text('services:\n  app:\n    environment:\n      OTHER_SETTING: preserved\n      POLL_CONFIG: old\n')
            running = root / 'running'
            restarts = root / 'restarts'
            docker = root / 'docker'
            docker.write_text('#!/bin/sh\ncase " $* " in\n*" config "*) exit 0;;\n*" ps "*) test ! -f "' + str(running) +
                '" || echo fixture-id;;\n*" up "*) touch "' + str(running) + '"; echo restart >> "' + str(restarts) + '";;\n*) exit 2;;\nesac\n')
            docker.chmod(0o700)
            fixture = root / 'fixture.yml'
            fixture.write_text(yaml.safe_dump([{'hosts': 'localhost', 'connection': 'local', 'gather_facts': False,
                'vars': {'doco_release_compose': str(compose), 'doco_release_repository': 'https://github.com/ai-workspace-infra/gitops.git',
                    'doco_release_reference': 'a' * 40, 'doco_release_target': 'candidate',
                    'doco_release_owner': str(os.getuid()), 'doco_release_group': str(os.getgid())},
                'roles': ['doco_cd_release_selection']}]))
            env = {**os.environ, 'PATH': str(root) + os.pathsep + os.environ['PATH'],
                   'ANSIBLE_CONFIG': str(ROOT / 'scripts/control_plane/ansible.cfg'), 'ANSIBLE_ROLES_PATH': str(ROOT / 'roles')}
            for i in range(2):
                result = subprocess.run(['ansible-playbook', '-i', 'localhost,', str(fixture)], env=env,
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                if i:
                    self.assertRegex(result.stdout, r'changed=0\s')
            self.assertEqual(restarts.read_text().splitlines(), ['restart'])
            self.assertIn('OTHER_SETTING: preserved', compose.read_text())
            self.assertIn('a' * 40, compose.read_text())
            fixture.write_text(yaml.safe_dump([{'hosts': 'localhost', 'connection': 'local', 'gather_facts': False,
                'tasks': [{'name': 'Private fixture output', 'ansible.builtin.debug': {'msg': 'must-never-be-printed'}, 'no_log': True},
                    {'name': 'Publish the owner component receipt without process or credential values',
                     'ansible.builtin.debug': {'msg': 'CONTROL_PLANE_EVIDENCE={"status":"candidate_runtime_ready","business_acceptance":false}'}}]}]))
            env.update(ANSIBLE_STDOUT_CALLBACK='control_plane_receipt',
                       ANSIBLE_CALLBACK_PLUGINS=str(ROOT / 'scripts/control_plane/callback_plugins'))
            result = subprocess.run(['ansible-playbook', '-i', 'localhost,', str(fixture)], env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn('must-never-be-printed', result.stdout)
            self.assertIn('CONTROL_PLANE_EVIDENCE=', result.stdout)

    def test_root_parent_is_created_and_identical_claim_reports_no_change(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory) / 'new-root-parent'
            contract = {'target': {'host': 'fixture'}, 'environment': 'uat', 'release': {'services': {}, 'accounts': {}},
                        'resolved': {'resource_id': 'fixture', 'data_disk': {'profile': 'root', 'data_path': str(parent / 'postgresql')}}}
            def inspect(c, state):
                return json.loads(state.read_text()) if state.exists() else {'binding': native_runtime.binding(c), 'initialized': False}
            with mock.patch.object(native_runtime, 'inspect', side_effect=inspect), mock.patch.object(native_runtime, 'verify_storage'):
                self.assertTrue(native_runtime.execute({'action': 'claim', 'contract': contract})['changed'])
                self.assertTrue(parent.is_dir())
                self.assertFalse(native_runtime.execute({'action': 'claim', 'contract': contract})['changed'])

    def test_unowned_nonempty_database_and_wrong_root_mount_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / 'postgresql'
            data.mkdir()
            (data / 'PG_VERSION').write_text('17')
            contract = {'resolved': {'data_disk': {'profile': 'root', 'data_path': str(data)}}}
            with mock.patch.object(native_runtime, 'verify_storage'):
                with self.assertRaises(native.Refused):
                    native_runtime.inspect(contract, Path(directory) / 'missing.json')
            with mock.patch.object(native, 'command', side_effect=['root-device', 'other-device']):
                with self.assertRaises(native.Refused):
                    native_runtime.verify_storage(contract['resolved']['data_disk'])

    def test_native_helpers_and_changed_roles_parse_and_entrypoints_pass_syntax(self):
        for path in [ROOT / 'roles/selfhost_control_plane/tasks/main.yml', ROOT / 'roles/selfhost_core_data_backup/tasks/main.yml',
                     ROOT / 'roles/selfhost_core_data_migration/tasks/main.yml', ROOT / 'roles/vhosts/Doco-CD/tasks/main.yml']:
            self.assertIsInstance(yaml.safe_load(path.read_text()), list)
        for entry in ('selfhost-control-plane.yml', 'selfhost-core-data-backup.yml', 'selfhost-core-data-migration.yml'):
            result = subprocess.run(['ansible-playbook', '-i', 'localhost,', str(ROOT / entry), '--syntax-check', '-e',
                '{"control_plane_contract":{"target":{"host":"localhost"}},"selfhost_core_data_backup_target":"localhost"}'],
                cwd=ROOT, env={**os.environ, 'ANSIBLE_CONFIG': str(ROOT / 'scripts/control_plane/ansible.cfg'),
                'ANSIBLE_ROLES_PATH': str(ROOT / 'roles')}, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name).resolve()
        self.client = self.directory / 'migratectl'
        self.client.write_text('#!/bin/sh\nexit 70\n')
        self.client.chmod(0o700)
        self.request = {'policy': migration_policy(), 'client': str(self.client), 'client_sha256': migration.digest(self.client)}
        self.plan = {'plan_id': str(uuid.uuid4()), 'plan_sha256': '1' * 64, 'state': 'preview', 'expires_at': utc(),
            'target_identity_sha256': '2' * 64, 'target_schema_sha256': '3' * 64, 'target_baseline_sha256': '4' * 64,
            'tool_sha256': '5' * 64, 'conflict_rows': 0, 'snapshot': {'snapshot_id': str(uuid.uuid4()), 'snapshot_sha256': '6' * 64}}

    def test_default_preview_calls_only_pinned_accounts_client_and_reference_args(self):
        with mock.patch.object(migration, 'invoke', return_value=self.plan) as invoke:
            result = migration.execute(self.request)
        self.assertEqual(result['action'], 'preview')
        self.assertEqual(invoke.call_args.args[0], self.client)
        self.assertIn('--source-token-env', invoke.call_args.args[1])
        self.assertNotIn('--apply', invoke.call_args.args[1])

    def test_pin_mismatch_bad_peer_expired_auth_and_shared_token_stop_before_any_client(self):
        for mutate in (lambda r: r.update(client_sha256='0' * 64),
                       lambda r: r['policy']['source'].update(allowed_target_urls=[]),
                       lambda r: r['policy']['target']['auth'].update(expires_at=utc(-3600)),
                       lambda r: r['policy']['target']['auth'].update(token_env='INTERNAL_SERVICE_TOKEN')):
            value = copy.deepcopy(self.request)
            mutate(value)
            with mock.patch.object(migration, 'invoke') as invoke, self.assertRaises(owner.Refused):
                migration.execute(value)
            invoke.assert_not_called()

    def test_new_apply_without_produced_recovery_proof_only_reads_status(self):
        request = {**self.request, 'action': 'apply', 'plan_id': self.plan['plan_id'], 'plan_sha256': self.plan['plan_sha256']}
        with mock.patch.object(migration, 'invoke', return_value=self.plan) as invoke:
            with self.assertRaisesRegex(owner.Refused, 'backup'):
                migration.execute(request)
        self.assertEqual(invoke.call_count, 1)
        self.assertIn('--status', invoke.call_args.args[1])

    def test_committed_retry_returns_authenticated_status_even_with_expired_old_plan_and_backup(self):
        request = {**self.request, 'action': 'apply', 'plan_id': self.plan['plan_id'], 'plan_sha256': self.plan['plan_sha256']}
        request['policy']['source']['auth']['expires_at'] = utc(-999)
        committed = {**self.plan, 'state': 'applied', 'expires_at': utc(-999)}
        with mock.patch.object(migration, 'invoke', return_value=committed) as invoke:
            self.assertEqual(migration.execute(request)['status'], 'already_applied')
        self.assertEqual(invoke.call_count, 1)
        with mock.patch.object(migration, 'invoke', return_value={**committed, 'plan_sha256': '9' * 64}):
            with self.assertRaises(owner.Refused):
                migration.execute(request)

    def test_no_legacy_fallback_on_unsupported_api(self):
        with mock.patch.object(migration.subprocess, 'run', return_value=mock.Mock(returncode=2, stderr='unsupported', stdout='')) as invoke:
            with self.assertRaisesRegex(owner.Refused, 'no fallback'):
                migration.execute(self.request)
        self.assertEqual(invoke.call_count, 1)

    def recovery(self):
        archive = self.directory / 'account.dump.enc'
        archive.write_bytes(b'fixture-encrypted-artifact-not-real-data')
        archive.chmod(0o600)
        cluster, oid = '123456789', '16384'
        self.plan['target_identity_sha256'] = adapter.hashed([cluster, oid])
        backup = {'status': 'passed', 'encrypted': True, 'durable': True, 'full_database': True,
            'environment': 'uat', 'source_database_id': self.plan['target_identity_sha256'],
            'baseline_id': self.plan['target_baseline_sha256'], 'schema_version': 2026102002, 'migration_rows': 1,
            'migration_dirty': 'false', 'schema_sha256': 'a' * 64, 'data_sha256': 'b' * 64,
            'archive_path': str(archive), 'archive_sha256': migration.digest(archive), 'database_system_identifier': cluster,
            'existing_users': 2, 'subscriptions': 1, 'checkpoint_id': 'uat_123'}
        restored = {**backup, 'isolated_restore_verified': True, 'restored_data_matches': True, 'source_restore_schema_matches': True}
        request = {'backup': backup, 'restore': restored, 'plan': self.plan,
            'fresh_plan': {**self.plan, 'plan_id': str(uuid.uuid4())}, 'plan_id': self.plan['plan_id'],
            'plan_sha256': self.plan['plan_sha256'], 'owner_sha': 'c' * 40}
        with mock.patch.object(adapter.subprocess, 'run', return_value=mock.Mock(returncode=0, stdout=cluster + ':' + oid)):
            gate, proof = adapter.build(request)
        receipt_path, proof_path = self.directory / 'gate.json', self.directory / 'proof.json'
        adapter.write_private(receipt_path, gate)
        adapter.write_private(proof_path, proof)
        return {**self.request, 'action': 'apply', 'plan_id': self.plan['plan_id'], 'plan_sha256': self.plan['plan_sha256'],
            'backup_receipt': str(receipt_path), 'backup_receipt_sha256': migration.digest(receipt_path),
            'owner_proof': str(proof_path), 'owner_proof_sha256': migration.digest(proof_path), 'owner_sha': 'c' * 40,
            'backup_archive': str(archive)}, request

    def test_real_format_adapter_gate_allows_exact_apply_after_readonly_status(self):
        request, _ = self.recovery()
        with mock.patch.object(migration, 'invoke', side_effect=[self.plan, {**self.plan, 'state': 'applied'}]) as invoke:
            self.assertEqual(migration.execute(request)['plan']['state'], 'applied')
        self.assertIn('--status', invoke.call_args_list[0].args[1])
        self.assertIn('--apply', invoke.call_args_list[1].args[1])
        gate = json.loads(Path(request['backup_receipt']).read_text())
        self.assertEqual(set(gate), {'format', 'backup_id', 'backup_sha256', 'target_identity_sha256', 'target_schema_sha256',
            'target_baseline_sha256', 'restored_baseline_sha256', 'restore_verified', 'verified_at', 'expires_at'})

    def test_changed_archive_or_proof_or_live_baseline_never_calls_apply(self):
        for bad in ('archive', 'proof', 'baseline'):
            with self.subTest(bad=bad):
                request, _ = self.recovery()
                current = copy.deepcopy(self.plan)
                if bad == 'archive':
                    Path(request['backup_archive']).write_bytes(b'changed')
                elif bad == 'proof':
                    request['owner_proof_sha256'] = '0' * 64
                else:
                    current['target_baseline_sha256'] = '9' * 64
                with mock.patch.object(migration, 'invoke', return_value=current) as invoke:
                    with self.assertRaises(owner.Refused):
                        migration.execute(request)
                self.assertEqual(invoke.call_count, 1)
                for name in ('gate.json', 'proof.json'):
                    (self.directory / name).unlink()

    def test_adapter_never_issues_gate_on_failed_restore_or_changed_canonical_baseline(self):
        _, request = self.recovery()
        for mutate in (lambda r: r['restore'].update(restored_data_matches=False),
                       lambda r: r['fresh_plan'].update(target_baseline_sha256='0' * 64),
                       lambda r: r['backup'].update(full_database=False)):
            value = copy.deepcopy(request)
            mutate(value)
            with self.assertRaises(ValueError):
                adapter.build(value)


if __name__ == '__main__':
    unittest.main()
