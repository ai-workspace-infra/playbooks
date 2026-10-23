"""Pinned listener contract, isolated route and Billing artifact regressions."""
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import unittest
import uuid
import sys
from unittest import mock
import test_owner as fixtures
import entrypoint as owner
import listener

target = fixtures.imported('listener_target', fixtures.ROOT / 'roles/selfhost_migration_listener/files/runtime.py')


class ListenerOwnerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RepositoryFixture()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.contract, _ = self.fixture.load()
        self.document = {'schema_version': 1, 'kind': 'selfhost-migration-listener', 'control_plane_config': 'candidate.yaml',
            'doco_target': 'candidate-migration', 'listener': {'source_url': 'https://source.example.invalid',
                'source_audience': 'source-migration', 'target_audience': 'target-migration', 'job_id': str(uuid.uuid4()),
                'allowed_peer_cidrs': ['192.0.2.0/24'], 'allowed_client_cidrs': ['127.0.0.1/32', '192.0.2.1/32'],
                'apply_enabled': False, 'initialize_receipts': False, 'receipt_owner_role': 'migration_owner'}}
        root = self.fixture.gitops
        self.compose = root / 'compose/listener.yml'
        self.services = owner.read_yaml(root / 'compose/application.yml')['services']
        self.services['caddy'] = {'image': 'ghcr.io/example/caddy@sha256:' + 'e' * 64,
            'container_name': 'web-saas-caddy', 'volumes': [{'type': 'bind', 'source': '/etc/xcontrol/migration-api/route.caddy',
                'target': '/etc/caddy/migration-listener.caddy', 'read_only': True, 'bind': {'create_host_path': False}}]}
        self.services['migration'] = {'image': self.fixture.images['accounts'], 'container_name': 'web-saas-migration-listener',
            'entrypoint': ['/usr/local/bin/migratectl'], 'command': listener.arguments(self.document['listener']),
            'network_mode': 'service:caddy', 'env_file': ['/etc/xcontrol/migration-api/listener.env'],
            'read_only': True, 'user': '0:0', 'cap_drop': ['ALL'], 'security_opt': ['no-new-privileges:true'],
            'volumes': [{'type': 'bind', 'source': '/data/migration-stage', 'target': '/private/target-stage',
                         'bind': {'create_host_path': False}}]}
        self.fixture.write(root / '.doco-cd.candidate-migration.yml', {'name': 'fixture', 'version': 'doco.v1',
            'working_dir': 'compose', 'compose_files': ['listener.yml'], 'env_files': [], 'remove_orphans': False})
        self.update()

    def update(self):
        self.fixture.write(self.compose, {'services': self.services})
        self.fixture.commit(self.fixture.gitops)

    def validate(self):
        return listener.validate(self.document, self.contract, self.fixture.gitops)

    def test_combined_pinned_selection_requires_no_source_execution_or_implicit_apply(self):
        self.document['example_only'] = False
        self.assertEqual(self.validate()['stage'], '/data/migration-stage')
        self.assertNotIn('--initialize-receipts', self.services['migration']['command'])
        ordinary = owner.selection(self.fixture.gitops, 'candidate', self.fixture.images)
        self.assertNotIn('migration', ordinary['images'])

    def test_actual_gitops_listener_schema_matches_owner(self):
        schema = os.environ.get('CONTROL_PLANE_GITOPS_LISTENER_SCHEMA')
        if not schema:
            self.skipTest('Set CONTROL_PLANE_GITOPS_LISTENER_SCHEMA to the reviewed schema')
        import jsonschema
        validator = jsonschema.Draft202012Validator(owner.read_yaml(Path(schema)))
        validator.validate(self.document)
        self.validate()
        for field in ('apply_enabled', 'initialize_receipts'):
            document = copy.deepcopy(self.document)
            document['listener'][field] = True
            self.assertTrue(list(validator.iter_errors(document)))

    def test_example_rejection_precedes_checkout_and_vault(self):
        self.fixture.write(self.fixture.gitops / 'listener.yaml', {**self.document, 'example_only': True})
        with mock.patch.object(sys, 'argv', ['listener.py', '--config', 'listener.yaml', '--gitops-ref', '0' * 40]), \
             mock.patch.object(owner, 'load_contract') as load, mock.patch.object(listener, 'resolve') as resolve:
            with self.assertRaises(owner.Refused):
                # main verifies its file belongs to PLAYBOOKS_CHECKOUT first.
                with mock.patch.dict(os.environ, {'PLAYBOOKS_CHECKOUT': str(fixtures.ROOT)}):
                    listener.main()
            load.assert_not_called()
            resolve.assert_not_called()

    def test_actual_listener_check_needs_no_vault_identity_and_leaves_both_checkouts_clean(self):
        root = self.fixture.gitops
        self.fixture.write(root / 'listener.yaml', self.document)
        self.fixture.commit(root)
        policy = self.fixture.directory / 'migration-policy.yaml'
        self.fixture.write(policy, {'vault_addr': 'https://vault.example.invalid', 'vault_token_env': 'MISSING_IDENTITY',
            'secret_refs': {key: 'kv/uat/accounts/migration/target#' + key for key in listener.KEYS}})
        policy.chmod(0o600)
        result = subprocess.run([sys.executable, str(self.fixture.owner / 'scripts/control_plane/listener.py'),
            '--config', 'listener.yaml', '--gitops-ref', self.fixture.git(root, 'rev-parse', 'HEAD'), '--check'],
            env={**os.environ, 'CONTROL_PLANE_MIGRATION_VARS_FILE': str(policy)}, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)['host_actions'])
        for checkout in (root, self.fixture.owner):
            self.assertEqual(self.fixture.git(checkout, 'status', '--porcelain', '--ignored'), '')

    def test_listener_refuses_host_ports_extra_mounts_unpinned_images_and_privilege(self):
        original = copy.deepcopy(self.services)
        for mutate in (lambda s: s['migration'].update(network_mode='host'),
                       lambda s: s['migration'].update(ports=['9097:9097']),
                       lambda s: s['migration'].update(image='ghcr.io/example/accounts:latest'),
                       lambda s: s['migration'].update(devices=['/dev/sda']),
                       lambda s: s['migration'].update(command=['serve', '--role', 'source']),
                       lambda s: s['migration'].update(env_file=['/run/control-plane-migration/listener.env']),
                       lambda s: s['caddy'].update(volumes=[])):
            self.services = copy.deepcopy(original)
            mutate(self.services)
            self.update()
            with self.assertRaises(owner.Refused):
                self.validate()

    def test_listener_rejects_examples_runtime_proof_in_gitops_and_broad_peers(self):
        for cfg in ({'allowed_peer_cidrs': ['0.0.0.0/0']}, {'initialize_receipts': True},
                    {'recovery': {}}, {'receipt_owner_role': 'account_user'}):
            document = copy.deepcopy(self.document)
            document['listener'].update(cfg)
            with self.assertRaises(owner.Refused):
                listener.validate(document, self.contract, self.fixture.gitops)
        self.document['example_only'] = True
        with self.assertRaises(owner.Refused):
            self.validate()

    def test_billing_source_checkout_digest_and_dirt_are_verified(self):
        root = self.fixture.directory / 'billing'
        root.mkdir()
        subprocess.run(['git', 'init', '-q', str(root)], check=True)
        self.fixture.git(root, 'config', 'user.name', 'Fixture')
        self.fixture.git(root, 'config', 'user.email', 'fixture@invalid.example')
        self.fixture.git(root, 'remote', 'add', 'origin', 'https://github.com/ai-workspace-services/billing-service.git')
        (root / 'sql').mkdir()
        sql = b'CREATE TABLE IF NOT EXISTS public.billing_fact (id bigint PRIMARY KEY);'
        (root / 'sql/billing-service-schema.sql').write_bytes(sql)
        self.fixture.commit(root)
        contract = copy.deepcopy(self.contract)
        contract['release']['billing'] = {'source_sha': self.fixture.git(root, 'rev-parse', 'HEAD'), 'schema_file': 'sql/billing-service-schema.sql'}
        contract['release']['accounts']['billing_schema_sha256'] = hashlib.sha256(sql).hexdigest()
        with mock.patch.dict(os.environ, {'BILLING_CHECKOUT': str(root)}):
            self.assertEqual(owner.billing_artifact(contract)['sql'], sql.decode())
            (root / 'unreviewed').write_text('dirty')
            with self.assertRaises(owner.Refused):
                owner.billing_artifact(contract)


class ListenerRuntimeTests(unittest.TestCase):
    def test_required_mounts_are_persistent_and_same_values_do_not_rewrite(self):
        self.assertEqual(listener.RUNTIME, '/etc/xcontrol/migration-api')
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'listener.env'
            with mock.patch.object(target, 'private', side_effect=lambda value: value):
                self.assertTrue(target.write(path, 'CURRENT=resolved\n'))
                original = path.stat().st_mtime_ns
                self.assertFalse(target.write(path, 'CURRENT=resolved\n'))
                self.assertEqual(original, path.stat().st_mtime_ns)
                self.assertTrue(target.write(path, 'CURRENT=new-resolved\n'))
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        import yaml
        document = yaml.safe_load((fixtures.ROOT / 'roles/selfhost_migration_listener/tasks/main.yml').read_text())
        block = next(item['block'] for item in document if 'block' in item)
        restart = next(item for item in block if item.get('ansible.builtin.command', {}).get('argv') == ['docker', 'restart', 'doco-cd'])
        self.assertIn('migration_listener_prepared', restart['when'])

    def test_dedicated_vault_identity_and_distinct_keys(self):
        policy = {'vault_addr': 'https://vault.example.invalid', 'vault_token_env': 'JOB_IDENTITY',
                  'secret_refs': {key: 'kv/uat/accounts/migration/target#' + key for key in listener.KEYS}}
        values = dict(zip(listener.KEYS, ['postgresql://migration_executor:encoded@stunnel-client:15432/account',
            base64.b64encode(b'a' * 32).decode(), base64.b64encode(b'b' * 32).decode()]))
        def run(argv, **kwargs):
            self.assertEqual(kwargs['env']['VAULT_TOKEN'], 'selected')
            self.assertNotIn('VAULT_AGENT_ADDR', kwargs['env'])
            return mock.Mock(returncode=0, stdout=json.dumps({'data': {'data': values}}))
        with mock.patch.dict(os.environ, {'JOB_IDENTITY': 'selected', 'VAULT_TOKEN': 'stale', 'VAULT_AGENT_ADDR': 'stale'}), \
             mock.patch.object(listener.subprocess, 'run', side_effect=run):
            self.assertEqual(listener.resolve(policy, 'uat'), values)
            values[listener.KEYS[2]] = values[listener.KEYS[1]]
            with self.assertRaises(owner.Refused):
                listener.resolve(policy, 'uat')

    @unittest.skipUnless(os.environ.get('CONTROL_PLANE_CADDY_FIXTURE') == '1', 'Explicit local Caddy fixture opt-in')
    def test_actual_caddy_adapts_scoped_readonly_exception(self):
        route = target.route_config('identity.example.invalid', ['127.0.0.1/32', '192.0.2.1/32'])
        config = '{\n admin off\n}\nhttps://identity.example.invalid {\n tls internal\n' + route + \
            '@writes method POST PUT PATCH DELETE\nhandle @writes {\n respond 403\n}\nrespond 200\n}\n'
        endpoint = subprocess.check_output(['docker', 'context', 'inspect', '--format', '{{.Endpoints.docker.Host}}'], text=True).strip()
        self.assertTrue(endpoint.startswith('unix://'))
        result = subprocess.run(['docker', 'run', '--rm', '-i', '--network', 'none', '--entrypoint', 'caddy',
            os.environ.get('CONTROL_PLANE_CADDY_IMAGE', 'caddy:2-alpine'), 'adapt', '--config', '/dev/stdin', '--adapter', 'caddyfile'],
            input=config, text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        adapted = json.loads(result.stdout)
        self.assertIn('127.0.0.1:9097', json.dumps(adapted))
        self.assertIn('/api/migrations/v1/*', json.dumps(adapted))
