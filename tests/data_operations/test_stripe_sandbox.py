"""Behavioral V3 scope, secret/config preservation and real PG checkpoint tests.

Uses two private local PG clusters when PG server binaries are available and
optional local Docker fixtures. No production, Vault or external network is
contacted by tests.
"""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / 'roles/web_saas_stripe_sandbox'
spec = importlib.util.spec_from_file_location('sandbox', ROLE / 'files/prepare.py')
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)


def contract(**values):
    return dict(generation='prod-v3', domain='svc.plus', release_tag='prod-v3-sandbox-20261018',
                run_id='unit-01', schema_version='2026101703', users=32,
                declaration=declaration(),
                iac_inventory_sha256='a'*64, iac_receipt_sha256='b'*64,
                stripe_key='sk_test_SyntheticSecret', webhook_secret='whsec_SyntheticWebhook', **values)


def declaration():
    return {
        'apiVersion': 'web-saas/v1', 'kind': 'StripeSandboxQualification',
        'target': dict(generation='prod-v3', resource_name='web-saas-prod-v3',
                       **{p+'_domain': p+'-selfhost-prod-v3.svc.plus' for p in ('console', 'accounts', 'billing', 'postgresql')},
                       public_domains={'console': 'console.svc.plus', 'accounts': 'accounts.svc.plus', 'billing': 'billing.svc.plus'},
                       doco_target='prod-v3-stripe-sandbox'),
        'database': dict(name='account', expected_schema_version=2026101703,
                         background_writers=False, preserve_existing_accounts=True),
        'stripe': dict(mode='test', api_version='2026-01-28.clover',
                       vault_path='kv/prod/billing-service', secret_key='SANDBOX_STRIPE_SECRET_KEY',
                       webhook_secret_key='SANDBOX_STRIPE_WEBHOOK_SECRET',
                       webhook_path='/api/billing/stripe/webhook',
                       webhook_environment='prod-v3', webhook_registry_ref='compose/web-saas/billing/stripe-webhooks.yaml',
                       webhook_url='https://accounts-selfhost-prod-v3.svc.plus/api/billing/stripe/webhook',
                       allowed_test_account_domain='example.invalid'),
        'isolation': dict(source_generation='prod-v2', source_access='none',
                          public_dns_cutover=False, postgres_public_port=False, smtp_enabled=False)}


def registry():
    return {'apiVersion': 'web-saas/v1', 'kind': 'StripeWebhookRegistry', 'domain_base': 'svc.plus',
            'webhook_path': '/api/billing/stripe/webhook',
            'environments': {'prod-v'+str(n): {'webhook_url': 'https://accounts-selfhost-prod-v'+str(n)+'.svc.plus/api/billing/stripe/webhook'} for n in (2, 3)}}


def original_env():
    values = dict(POSTGRES_PASSWORD='preserved-postgres', RECOVERY_APP_PASSWORD='a' * 64,
                  AUTH_TOKEN_PUBLIC_TOKEN='preserved-public', AUTH_TOKEN_REFRESH_SECRET='preserved-refresh',
                  AUTH_TOKEN_ACCESS_SECRET='preserved-access', INTERNAL_SERVICE_TOKEN='preserved-service',
                  DATABASE_BACKGROUND_WRITERS='false', DATABASE_URL='"host=postgres port=5432 dbname=account user=recovery_app password=' + 'a'*64 + ' sslmode=disable"',
                  STRIPE_SECRET_KEY='', STRIPE_WEBHOOK_SECRET='', UNRELATED='preserved')
    return ''.join(k + '=' + v + '\n' for k, v in values.items()).encode()


def account():
    import jinja2
    template = (ROOT / 'roles/web_saas_generation_recovery/templates/account.yaml.j2').read_text()
    return jinja2.Template(template, undefined=jinja2.StrictUndefined).render(recovery={
        'target': {'generation': 'prod-v3', 'domain_base': 'svc.plus',
                   'inventory_host': 'console-selfhost-prod-v3.svc.plus'}}).encode()


class SafetyTests(unittest.TestCase):
    def test_declared_webhook_domain_vault_keys_and_mode_fail_closed(self):
        for section, key, value in [
            ('stripe', 'webhook_url', 'https://accounts-selfhost-prod-v2.svc.plus/api/billing/stripe/webhook'),
            ('stripe', 'webhook_path', '/api/other'), ('stripe', 'mode', 'live'),
            ('stripe', 'vault_path', 'kv/prod/accounts'), ('stripe', 'secret_key', 'STRIPE_SECRET_KEY'),
            ('stripe', 'webhook_secret_key', 'LIVE_STRIPE_WEBHOOK_SECRET'),
            ('stripe', 'webhook_environment', 'prod-v2'),
            ('target', 'accounts_domain', 'accounts.svc.plus'),
            ('target', 'doco_target', 'prod-v2'), ('isolation', 'source_access', 'read')]:
            c = contract()
            c['declaration'][section][key] = value
            with self.subTest(section=section, key=key), self.assertRaises(ValueError):
                s.contract(c, 'web-saas-prod-v3')

    def test_only_v3_and_exact_real_hostname(self):
        s.contract(contract(), 'web-saas-prod-v3')
        for key, bad in [('generation', 'prod-v2'), ('generation', 'prod-v4'),
                         ('release_tag', '../../bad'), ('release_tag', 'prod-v2-release'),
                         ('run_id', '../escape'), ('run_id', '.'), ('domain', 'svc.plus { tls internal }')]:
            c = contract()
            c[key] = bad
            with self.subTest(key=key, bad=bad), self.assertRaises(ValueError):
                s.contract(c, 'web-saas-prod-v3')
        for hostname in ('web-saas-prod-v2', 'web-saas-prod', 'web-saas-prod-v30'):
            with self.assertRaises(ValueError):
                s.contract(contract(), hostname)

    def test_accounts_env_preserves_every_other_value(self):
        original = original_env()
        c = contract()
        new = s.env_values(s.sandbox_env(original, c))
        old = s.env_values(original)
        for key, value in old.items():
            if key not in ('STRIPE_SECRET_KEY', 'STRIPE_WEBHOOK_SECRET'):
                self.assertEqual(new[key], value)
        self.assertEqual(new['STRIPE_SECRET_KEY'], c['stripe_key'])
        self.assertEqual(new['STRIPE_WEBHOOK_SECRET'], c['webhook_secret'])
        self.assertEqual(new['STRIPE_FRONTEND_URL'], 'https://console-selfhost-prod-v3.svc.plus')
        self.assertEqual(original, original_env())

    def test_live_keys_whitespace_injection_missing_and_duplicate_credentials_fail(self):
        for key, value in [('stripe_key', 'sk_live_No'), ('stripe_key', ''),
                           ('stripe_key', 'sk_test_A\nOTHER=bad'), ('webhook_secret', 'bad'),
                           ('webhook_secret', 'whsec_bad\r')]:
            c = contract()
            c[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                s.sandbox_env(original_env(), c)
        for old in (original_env().replace(b'DATABASE_BACKGROUND_WRITERS=false', b'DATABASE_BACKGROUND_WRITERS=true'),
                    original_env() + b'AUTH_TOKEN_PUBLIC_TOKEN=duplicate\n',
                    original_env().replace(b'host=postgres ', b'host=source ')):
            with self.assertRaises(ValueError):
                s.sandbox_env(old, contract())

    def test_account_yaml_urls_and_auth_placeholders(self):
        result = yaml.safe_load(s.account_config(account(), contract()))
        self.assertEqual(result['server']['publicUrl'], 'https://accounts-selfhost-prod-v3.svc.plus')
        self.assertEqual(result['auth']['oauth']['frontendUrl'], 'https://console-selfhost-prod-v3.svc.plus')
        self.assertEqual(result['server']['allowedOrigins'], ['https://console-selfhost-prod-v3.svc.plus'])
        self.assertEqual(result['store']['dsn'], '${DATABASE_URL}')
        self.assertEqual(result['auth']['token']['accessSecret'], '${AUTH_TOKEN_ACCESS_SECRET}')

    def test_caddy_shared_public_sni_uses_restored_certificate_and_isolates_routes(self):
        text = s.caddy_config(contract()).decode()
        self.assertIn('http_port 80', text)
        self.assertIn('https_port 443', text)
        self.assertNotIn('tls internal', text)
        self.assertNotIn('auto_https disable', text)
        self.assertNotIn(':8443', text)
        self.assertNotIn('5432', text)
        self.assertNotIn('prod-v2', text)
        for alias in ('console', 'accounts', 'billing', 'postgresql'):
            self.assertIn(alias + '-selfhost-prod-v3.svc.plus', text)
        for domain in ('console.svc.plus', 'accounts.svc.plus', 'billing.svc.plus'):
            self.assertIn(domain + ' {', text)
            self.assertIn('tls /etc/caddy/tls/svc.plus/current/fullchain.pem /etc/caddy/tls/svc.plus/current/key.pem', text)
        self.assertNotIn('postgresql-selfhost-prod-v3.svc.plus {', text)
        self.assertNotIn('postgresql.svc.plus {', text)
        self.assertIn('handle /api/billing/stripe/webhook {\n        reverse_proxy accounts:8080', text)
        console_block = text.split('console-selfhost-prod-v3.svc.plus {')[1].split('accounts-selfhost-prod-v3.svc.plus {')[0]
        self.assertEqual(console_block.strip(), 'reverse_proxy console:3000\n}')
        self.assertNotIn('accounts:8080', console_block)
        self.assertNotIn('method', console_block)
        self.assertNotIn('respond', console_block)
        self.assertIn('path /readyz /healthz /api/ping', text)
        accounts_block = text.split('accounts-selfhost-prod-v3.svc.plus {')[1].split('billing-selfhost')[0]
        self.assertNotIn('respond', accounts_block)

    def test_all_exceptions_are_redacted(self):
        for error in (ValueError('sk_live_SECRET postgres-password'),
                      subprocess.CalledProcessError(1, ['private-command'], stderr=b'whsec_SECRET')):
            stdout, stderr = io.StringIO(), io.StringIO()
            with mock.patch.object(s, 'main', side_effect=error), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as stopped:
                    s.cli()
            self.assertEqual(stopped.exception.code, 2)
            self.assertEqual(stdout.getvalue(), '')
            self.assertEqual(json.loads(stderr.getvalue())['result'], 'stopped')
            self.assertNotIn('SECRET', stderr.getvalue())
            self.assertNotIn('password', stderr.getvalue())

    def test_restore_failure_prevents_any_configuration_or_role_change(self):
        with tempfile.TemporaryDirectory(dir=ROOT, prefix='.stripe-config-') as tmp:
            config = Path(tmp)
            for name, data in {'secrets.env': original_env(), 'account.yaml': account(), 'Caddyfile': b'old'}.items():
                s.write_new(config / name, data)
            with mock.patch.object(s, 'CONFIG', config), mock.patch.object(s, 'validate_caddy_config'), mock.patch.object(s, 'checkpoint', side_effect=ValueError('restore mismatch')), mock.patch.object(s.DockerPG, 'sql') as sql:
                with self.assertRaises(ValueError):
                    s.prepare(contract(), config / 'run', 'sha256:'+'a'*64, {})
                self.assertEqual((config / 'secrets.env').read_bytes(), original_env())
                self.assertEqual((config / 'account.yaml').read_bytes(), account())
                self.assertEqual((config / 'Caddyfile').read_bytes(), b'old')
                self.assertFalse((config / 'stripe-sandbox.env').exists())
                sql.assert_not_called()

    def test_success_preserves_original_checksum_and_private_files(self):
        with tempfile.TemporaryDirectory(dir=ROOT, prefix='.stripe-config-') as tmp:
            config = Path(tmp)
            for name, data in {'secrets.env': original_env(), 'account.yaml': account(), 'Caddyfile': b'old'}.items():
                s.write_new(config / name, data)
            original_hash = s.sha(config / 'secrets.env')
            evidence = {'schema_version': '2026101703', 'counts': {'public.users': 32},
                        'dirty': False, 'restore_verified': True}
            with mock.patch.object(s, 'CONFIG', config), mock.patch.object(s, 'validate_caddy_config'), mock.patch.object(s, 'checkpoint', return_value=evidence), mock.patch.object(s, 'verify_prepared'), mock.patch.object(s, 'run'), mock.patch.object(s.DockerPG, 'sql') as sql:
                s.prepare(contract(), config / 'run', 'sha256:'+'a'*64, {})
                self.assertEqual(s.sha(config / 'secrets.env'), original_hash)
                self.assertEqual(s.env_values((config / 'stripe-sandbox.env').read_bytes())['STRIPE_SECRET_KEY'], contract()['stripe_key'])
                s.private(config / 'stripe-sandbox.env')
                s.private(config / 'run', directory=True)
                self.assertIn('default_transaction_read_only=off', sql.call_args.args[0])
                self.assertNotIn('PASSWORD', sql.call_args.args[0])
                with self.assertRaises(FileExistsError):
                    s.prepare(contract(), config / 'run', 'sha256:'+'a'*64, {})

    def test_symlinks_and_public_permissions_rejected(self):
        with tempfile.TemporaryDirectory(dir=ROOT, prefix='.stripe-perms-') as tmp:
            path = Path(tmp) / 'private'
            s.write_new(path, b'secret')
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                s.private(path)
            link = Path(tmp) / 'link'
            link.symlink_to(path)
            with self.assertRaises(ValueError):
                s.replace_private(link, b'replacement')
            self.assertEqual(path.read_bytes(), b'secret')

    def test_vault_reads_only_fields_and_every_secret_task_is_no_log(self):
        tasks = yaml.safe_load((ROLE / 'tasks/prepare.yml').read_text())
        vault = next(t for t in tasks if 'register' in t and t['register'] == 'stripe_vault_fields')
        tls_vault = next(t for t in tasks if 'register' in t and t['register'] == 'stripe_tls_vault_fields')
        self.assertEqual(vault['ansible.builtin.command']['argv'],
                         ['vault', 'kv', 'get', '-field={{ item }}', '{{ stripe_manifest.stripe.vault_path }}'])
        self.assertEqual(vault['loop'], ['{{ stripe_manifest.stripe.secret_key }}', '{{ stripe_manifest.stripe.webhook_secret_key }}'])
        self.assertEqual(vault['delegate_to'], 'localhost')
        self.assertEqual(tls_vault['ansible.builtin.command']['argv'],
                         ['vault', 'kv', 'get', '-field={{ item }}', 'kv/CICD/domains/svc.plus'])
        self.assertEqual(tls_vault['loop'], ['tls_fullchain_pem_b64', 'tls_cert_pem_b64', 'tls_key_pem_b64', 'tls_ca_pem_b64', 'tls_trust_bundle_pem_b64'])
        for task in tasks:
            if task.get('register') in ('stripe_vault_fields', 'stripe_tls_vault_fields', 'stripe_prepared'):
                self.assertIs(task['no_log'], True)
        restore = next(t for t in tasks if t.get('name') == 'Restore the shared-domain certificate through the validated file owner')
        self.assertIs(restore['no_log'], True)
        self.assertNotIn('doco_cd_release_selection', (ROLE / 'tasks/main.yml').read_text())

    def test_source_host_is_rejected_before_any_command(self):
        with mock.patch.object(s.sys, 'argv', ['prepare.py', 'prepare']), mock.patch.object(s.sys, 'stdin', io.StringIO(json.dumps(contract()))), mock.patch.object(s.socket, 'gethostname', return_value='web-saas-prod-v2'), mock.patch.object(s, 'inspect_live') as inspect, mock.patch.object(s, 'run') as run:
            with self.assertRaises(ValueError):
                s.main()
            inspect.assert_not_called()
            run.assert_not_called()

    def test_isolated_restore_never_receives_live_mount_network_ports_or_secrets(self):
        calls = []
        image = 'sha256:' + 'a'*64
        def run(args, **kwargs):
            calls.append(args)
            if args[1:3] == ['network', 'inspect']:
                return json.dumps([{'Internal': True}]).encode()
            if args[1] == 'inspect':
                return json.dumps([{'HostConfig': {'NetworkMode': args[2], 'PortBindings': {}},
                                    'Mounts': [{'Type': 'volume'}], 'Image': image}]).encode()
            return b''
        with mock.patch.object(s, 'run', side_effect=run):
            with s.isolated_pg(image) as db:
                self.assertNotEqual(db.container, s.LIVE)
        create = next(args for args in calls if args[1] == 'create')
        self.assertIn('type=volume,destination=/var/lib/postgresql/data', create)
        self.assertNotIn('--publish', create)
        self.assertNotIn('/data/postgresql', ' '.join(create))
        self.assertNotIn('STRIPE', ' '.join(create))
        self.assertIn('--internal', calls[0])
        self.assertEqual(calls[-2][1:4], ['rm', '--force', '--volumes'])
        self.assertEqual(calls[-1][1:3], ['network', 'rm'])

    def test_exposed_or_nonisolated_temporary_pg_fails_and_cleans_only_its_resources(self):
        for ports, internal in [({'5432/tcp': [{'HostPort': '5432'}]}, True), ({}, False)]:
            calls = []
            image = 'sha256:' + 'a'*64
            def run(args, **kwargs):
                calls.append(args)
                if args[1:3] == ['network', 'inspect']:
                    return json.dumps([{'Internal': internal}]).encode()
                if args[1] == 'inspect':
                    return json.dumps([{'HostConfig': {'NetworkMode': args[2], 'PortBindings': ports},
                                        'Mounts': [], 'Image': image}]).encode()
                return b''
            with mock.patch.object(s, 'run', side_effect=run):
                with self.assertRaises(ValueError):
                    with s.isolated_pg(image):
                        self.fail('must not yield unsafe container')
            self.assertFalse(any(args[1] == 'start' for args in calls))
            self.assertTrue(all(s.LIVE not in args for args in calls))
            self.assertEqual(calls[-1][1:3], ['network', 'rm'])


@unittest.skipUnless(shutil.which('ansible-playbook'), 'Ansible CLI required')
class InventoryGuardTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('STRIPE_TEST_IAC_INVENTORY'), 'Optional read-only acceptance against supplied controller artifacts')
    def test_supplied_controller_artifacts_pass_without_remote_tasks(self):
        tasks = yaml.safe_load((ROLE / 'tasks/main.yml').read_text())
        end = next(i for i, task in enumerate(tasks) if task['name'] == 'Read the actual host identity and persistent data mount')
        tasks = tasks[:end]
        tasks[1]['ansible.builtin.include_tasks'] = str(ROLE / 'tasks/provenance.yml')
        variables = yaml.safe_load((ROLE / 'defaults/main.yml').read_text())
        variables.update(web_saas_stripe_target='console-selfhost-prod-v3.svc.plus',
                         web_saas_stripe_release_tag='prod-v3-stripe-sandbox-test', web_saas_stripe_run_id='controller-test',
                         web_saas_stripe_iac_receipt_file=os.environ['STRIPE_TEST_IAC_RECEIPT'],
                         web_saas_stripe_manifest_file=os.environ['STRIPE_TEST_MANIFEST'],
                         web_saas_stripe_webhook_registry_file=os.environ['STRIPE_TEST_REGISTRY'])
        with tempfile.TemporaryDirectory(dir=ROOT, prefix='.stripe-controller-') as tmp:
            entry = Path(tmp) / 'controller.yml'
            entry.write_text(yaml.safe_dump([{'hosts': variables['web_saas_stripe_target'], 'gather_facts': False,
                                              'vars': variables, 'tasks': tasks}]))
            result = subprocess.run(['ansible-playbook', '-i', 'inventory/terraform_cmdb.py', str(entry)], cwd=ROOT,
                                    capture_output=True, text=True, timeout=30,
                                    env=dict(os.environ, AI_WORKSPACE_CMDB_JSON=os.environ['STRIPE_TEST_IAC_INVENTORY']))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotIn('Read the actual host identity', result.stdout)

    def execute(self, changes=None, variables=None, receipt_changes=None, manifest_changes=None, registry_changes=None, guards_only=False):
        host = 'console-selfhost-prod-v3.svc.plus'
        defaults = yaml.safe_load((ROLE / 'defaults/main.yml').read_text())
        defaults.update(web_saas_stripe_target=host, web_saas_stripe_release_tag='prod-v3-sandbox-test',
                        web_saas_stripe_run_id='test')
        record = {'name': 'web-saas-prod-v3', 'ip': '127.0.0.1', 'groups': ['web_saas', 'web_saas_recovery'],
                  'host_vars': {'service_domains': [p+'-selfhost-prod-v3.svc.plus' for p in ('console', 'accounts', 'billing', 'postgresql')],
                                'ansible_connection': 'local'}}
        inventory = {host: record, 'web-saas-prod': {'name': 'web-saas-prod', 'ip': '192.0.2.99', 'groups': ['web_saas']}}
        changes = changes or {}
        if 'cmdb_name' in changes:
            record['name'] = changes['cmdb_name']
        if 'service_domains' in changes:
            record['host_vars']['service_domains'] = changes['service_domains']
        if 'cmdb_environment' in changes:
            inventory['environment'] = changes['cmdb_environment']
        tasks = yaml.safe_load((ROLE / 'tasks/main.yml').read_text())
        guard_end = next(i for i, task in enumerate(tasks) if task['name'] == 'Read the actual host identity and persistent data mount')
        guards = tasks[:guard_end]
        guards[1]['ansible.builtin.include_tasks'] = str(ROLE / 'tasks/provenance.yml')
        with tempfile.TemporaryDirectory(dir=ROOT, prefix='.stripe-inventory-') as tmp:
            manifest = Path(tmp) / 'manifest.yml'
            declared = declaration()
            del declared['stripe']['webhook_url']
            if manifest_changes:
                for section, key, value in manifest_changes:
                    declared[section][key] = value
            manifest.write_text(yaml.safe_dump(declared))
            defaults['web_saas_stripe_manifest_file'] = str(manifest)
            webhook_registry = Path(tmp) / 'registry.yml'
            webhook_registry.write_text(yaml.safe_dump(registry() | (registry_changes or {})))
            defaults['web_saas_stripe_webhook_registry_file'] = str(webhook_registry)
            source, entry = Path(tmp) / 'inventory.json', Path(tmp) / 'guard.yml'
            source.write_text(json.dumps(inventory))
            receipt = Path(tmp) / 'receipt.json'
            receipt.write_text(json.dumps(dict(environment='prod', result='applied_verified', project='svc.plus',
                                              provider='gcp-cloud', inventory_sha256=s.sha(source)) | (receipt_changes or {})))
            defaults['web_saas_stripe_iac_receipt_file'] = str(receipt)
            defaults.update(variables or {})
            play = [{'hosts': host, 'gather_facts': False, 'vars': defaults, 'tasks': guards}]
            entry.write_text(yaml.safe_dump(play))
            result = subprocess.run(['ansible-playbook', '-i', 'inventory/terraform_cmdb.py',
                                     str(entry) if guards_only else 'web-saas-stripe-sandbox.yml', '-e', json.dumps(defaults)],
                                    cwd=ROOT, capture_output=True, text=True, timeout=30,
                                    env=dict(os.environ, AI_WORKSPACE_CMDB_JSON=str(source), ANSIBLE_NOCOLOR='1'))
            return result

    def test_exact_iac_v3_record_without_environment_passes_with_paired_receipt(self):
        result = self.execute(guards_only=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_rejects_wrong_cmdb_domains_generation_and_multi_host_before_connection(self):
        cases = [({'cmdb_name': 'web-saas-prod-v2'}, {}),
                 ({'service_domains': ['console-selfhost-prod-v3.svc.plus']}, {}),
                 ({}, {'web_saas_stripe_generation': 'prod-v4'}),
                 ({}, {'web_saas_stripe_target': 'all'}),
                 ({'cmdb_environment': 'uat'}, {})]
        for changes, variables in cases:
            result = self.execute(changes=changes, variables=variables)
            with self.subTest(changes=changes, variables=variables):
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertNotIn('Read the actual host identity', result.stdout)

    def test_wrong_receipt_environment_and_inventory_checksum_fail_before_connection(self):
        for changes in ({'environment': 'uat'}, {'inventory_sha256': '0'*64}, {'result': 'planned'}):
            result = self.execute(receipt_changes=changes)
            with self.subTest(changes=changes):
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertNotIn('Read the actual host identity', result.stdout)

    def test_wrong_declared_webhook_and_live_vault_key_fail_before_connection(self):
        for changes in ([('stripe', 'webhook_url', 'https://accounts.svc.plus/api/billing/stripe/webhook')],
                        [('stripe', 'secret_key', 'STRIPE_SECRET_KEY')]):
            result = self.execute(manifest_changes=changes)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertNotIn('Read the actual host identity', result.stdout)

    def test_registry_mismatch_and_v2_selection_fail_before_connection(self):
        for changes in ({'domain_base': 'other.example'}, {'webhook_path': '/bad'},
                        {'environments': {'prod-v3': {'webhook_url': registry()['environments']['prod-v2']['webhook_url']}}}):
            result = self.execute(registry_changes=changes)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertNotIn('Read the actual host identity', result.stdout)
        result = self.execute(manifest_changes=[('stripe', 'webhook_environment', 'prod-v2')])
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertNotIn('Read the actual host identity', result.stdout)


class NativePG(s.DockerPG):
    def __init__(self, bindir, directory):
        self.bindir, self.directory = bindir, directory
        self.container = 'isolated-native-test'

    def command(self, program, *args):
        return [str(self.bindir / program), '-h', str(self.directory / 'socket'),
                '-p', '55437', '-U', 'postgres', *args]

    def restore(self, path):
        s.run(self.command('createdb', '--template=template0', 'account'))
        toc = s.run([str(self.bindir / 'pg_restore'), '--list', str(path)]).decode()
        if 'SCHEMA - public ' in toc:
            self.sql('DROP SCHEMA public;')
        s.run(self.command('pg_restore', '--dbname=account', '--no-owner', '--no-privileges',
                           '--single-transaction', '--exit-on-error', str(path)))


@contextlib.contextmanager
def cluster(bindir, parent):
    with tempfile.TemporaryDirectory(dir=parent, prefix='pg-') as tmp:
        base = Path(tmp)
        (base / 'socket').mkdir(mode=0o700)
        s.run([str(bindir / 'initdb'), '-D', str(base / 'data'), '-A', 'trust', '-U', 'postgres', '--no-locale'])
        s.run([str(bindir / 'pg_ctl'), '-D', str(base / 'data'), '-l', str(base / 'log'),
               '-o', "-k " + str(base / 'socket') + " -h '' -p 55437", '-w', 'start'])
        try:
            yield NativePG(bindir, base)
        finally:
            s.run([str(bindir / 'pg_ctl'), '-D', str(base / 'data'), '-m', 'immediate', '-w', 'stop'])


@unittest.skipUnless(shutil.which('pg_config') and os.geteuid() != 0, 'Native PostgreSQL binaries and non-root user required')
class RealPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bindir = Path(os.environ.get('STRIPE_TEST_PG_BINDIR') or subprocess.check_output(['pg_config', '--bindir'], text=True).strip())
        if not (cls.bindir / 'initdb').exists() or not (cls.bindir / 'postgres').exists():
            raise unittest.SkipTest('PostgreSQL server binaries unavailable')

    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        # Canonical short path keeps Unix socket paths below PostgreSQL's limit.
        tmp = self.stack.enter_context(tempfile.TemporaryDirectory(dir=ROOT, prefix='.pg-'))
        self.parent = Path(tmp).resolve()
        self.source = self.stack.enter_context(cluster(self.bindir, self.parent))
        self.restored = self.stack.enter_context(cluster(self.bindir, self.parent))
        s.run(self.source.command('createdb', 'account'))
        self.source.sql('''CREATE TABLE schema_migrations(version bigint PRIMARY KEY, dirty boolean NOT NULL);
INSERT INTO schema_migrations VALUES (2026101703,false);
CREATE TABLE users(id serial PRIMARY KEY); INSERT INTO users SELECT generate_series(1,32);
CREATE TABLE subscription_records(id serial PRIMARY KEY); INSERT INTO subscription_records VALUES (1),(2);
CREATE TABLE billing_ledger(id serial PRIMARY KEY);
INSERT INTO billing_ledger VALUES (1);
CREATE SCHEMA finance; CREATE TABLE finance.invoices(id serial PRIMARY KEY);
CREATE ROLE recovery_app LOGIN; ALTER ROLE recovery_app SET default_transaction_read_only=on;''')
        self.root = self.parent / 'checkpoint'
        self.root.mkdir(mode=0o700)

    def checkpoint(self):
        return s.checkpoint(self.source, lambda: contextlib.nullcontext(self.restored), self.root, contract())

    def test_real_custom_archive_independent_restore_and_permissions(self):
        receipt = self.checkpoint()
        self.assertTrue(receipt['restore_verified'])
        self.assertEqual(receipt['counts']['public.users'], 32)
        self.assertEqual(receipt['counts']['public.subscription_records'], 2)
        self.assertEqual(receipt['finance_counts'], {'finance.invoices': 0})
        self.assertEqual(receipt['counts']['public.billing_ledger'], 1)
        self.assertEqual(receipt['schema_version'], '2026101703')
        self.assertEqual(receipt['webhook_url'], declaration()['stripe']['webhook_url'])
        self.assertFalse(receipt['dirty'])
        self.assertEqual(receipt['server_version_num'], self.restored.sql("SHOW server_version_num"))
        s.private(self.root / 'account.dump')
        s.private(self.root / 'checkpoint.json')
        self.assertEqual((self.root / 'account.dump').read_bytes()[:5], b'PGDMP')

    def test_exported_snapshot_counts_and_dump_remain_consistent_during_writes(self):
        dump = self.source.dump
        def concurrent_dump(path, snapshot):
            self.source.sql('INSERT INTO users VALUES (33);')
            dump(path, snapshot)
        with mock.patch.object(self.source, 'dump', side_effect=concurrent_dump):
            self.checkpoint()
        self.assertEqual(self.source.sql('SELECT count(*) FROM users;'), '33')
        self.assertEqual(self.restored.sql('SELECT count(*) FROM users;'), '32')

    def test_dirty_schema_fails_before_dump(self):
        self.source.sql('UPDATE schema_migrations SET dirty=true;')
        with self.assertRaises(ValueError):
            self.checkpoint()
        self.assertFalse((self.root / 'account.dump').exists())
        self.assertFalse((self.root / 'checkpoint.json').exists())

    def test_restored_count_mismatch_does_not_issue_readiness_receipt(self):
        restore = self.restored.restore
        def wrong_restore(path):
            restore(path)
            self.restored.sql('DELETE FROM subscription_records WHERE id=1;')
        with mock.patch.object(self.restored, 'restore', side_effect=wrong_restore):
            with self.assertRaises(ValueError):
                self.checkpoint()
        self.assertTrue((self.root / 'account.dump').exists())
        self.assertFalse((self.root / 'checkpoint.json').exists())

    def test_nonempty_finance_is_preserved_and_counted(self):
        self.source.sql('INSERT INTO finance.invoices VALUES (1);')
        receipt = self.checkpoint()
        self.assertEqual(receipt['finance_counts']['finance.invoices'], 1)
        self.assertEqual(self.restored.sql('SELECT count(*) FROM finance.invoices;'), '1')

    def test_restored_schema_mismatch_does_not_issue_receipt(self):
        restore = self.restored.restore
        def wrong_restore(path):
            restore(path)
            self.restored.sql('UPDATE schema_migrations SET version=2026101702;')
        with mock.patch.object(self.restored, 'restore', side_effect=wrong_restore):
            with self.assertRaises(ValueError):
                self.checkpoint()
        self.assertFalse((self.root / 'checkpoint.json').exists())

    def test_existing_role_can_write_only_after_readonly_default_is_disabled(self):
        self.source.sql('GRANT SELECT,INSERT ON users TO recovery_app;')
        command = self.source.command('psql', '-U', 'recovery_app', '-d', 'account', '-XAtq',
                                      '-v', 'ON_ERROR_STOP=1', '-c', 'INSERT INTO users VALUES (33);')
        with self.assertRaises(subprocess.CalledProcessError):
            s.run(command)
        s.enable_account_writes(self.source)
        s.run(command)
        self.assertEqual(self.source.sql('SELECT count(*) FROM users;'), '33')
        self.assertEqual(self.source.sql("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname='recovery_app';"), 'f')


@unittest.skipUnless(os.environ.get('STRIPE_TEST_DOCKER_IMAGE'), 'Set STRIPE_TEST_DOCKER_IMAGE=postgres:17 for container acceptance')
class DockerPostgresTests(unittest.TestCase):
    def test_nonempty_invoices_payments_refunds_and_dynamic_users_survive_restore(self):
        image = json.loads(s.run(['docker', 'image', 'inspect', os.environ['STRIPE_TEST_DOCKER_IMAGE']]))[0]['Id']
        c = contract()
        c['users'] = None  # Discover from the snapshot, including after earlier tests.
        actual = {}
        with tempfile.TemporaryDirectory(dir=ROOT, prefix='.stripe-docker-') as tmp:
            root = Path(tmp).resolve()
            with s.isolated_pg(image) as source:
                s.run(source.command('createdb', 'account'))
                source.sql('''CREATE TABLE schema_migrations(version bigint PRIMARY KEY, dirty boolean NOT NULL);
INSERT INTO schema_migrations VALUES (2026101703,false);
CREATE TABLE users(id serial PRIMARY KEY); INSERT INTO users SELECT generate_series(1,35);
CREATE TABLE subscription_records(id serial PRIMARY KEY); INSERT INTO subscription_records VALUES (1),(2);
CREATE TABLE billing_ledger(id serial PRIMARY KEY); INSERT INTO billing_ledger VALUES (1);
CREATE SCHEMA finance;
CREATE TABLE finance.invoices(id serial PRIMARY KEY, amount integer, memo text);
INSERT INTO finance.invoices(amount,memo) VALUES (1500,'sandbox invoice'),(2000,'second invoice');
CREATE TABLE finance.payments(id serial PRIMARY KEY, amount integer, status text);
INSERT INTO finance.payments(amount,status) VALUES (1500,'paid');
CREATE TABLE finance.refunds(id serial PRIMARY KEY, amount integer, reason text);
INSERT INTO finance.refunds(amount,reason) VALUES (500,'partial refund');''')
                expected = {table: source.sql('SELECT json_agg(t ORDER BY id)::text FROM finance.'+table+' t;')
                            for table in ('invoices', 'payments', 'refunds')}
                @contextlib.contextmanager
                def restore_target():
                    with s.isolated_pg(image) as restored:
                        yield restored
                        for table in expected:
                            actual[table] = restored.sql('SELECT json_agg(t ORDER BY id)::text FROM finance.'+table+' t;')
                receipt = s.checkpoint(source, restore_target, root, c)
                self.assertEqual(actual, expected)
                self.assertEqual(receipt['expected_users'], 35)
                self.assertEqual(receipt['counts']['public.users'], 35)
                self.assertEqual(receipt['finance_counts'], {'finance.invoices': 2, 'finance.payments': 1, 'finance.refunds': 1})
                self.assertTrue(receipt['restore_verified'])
                for table, data in expected.items():
                    self.assertEqual(source.sql('SELECT json_agg(t ORDER BY id)::text FROM finance.'+table+' t;'), data)

    def test_real_docker17_snapshot_checkpoint_and_isolated_restore(self):
        image = json.loads(s.run(['docker', 'image', 'inspect', os.environ['STRIPE_TEST_DOCKER_IMAGE']]))[0]['Id']
        with tempfile.TemporaryDirectory(dir=ROOT, prefix='.stripe-docker-') as tmp:
            root = Path(tmp).resolve()
            with s.isolated_pg(image) as source:
                s.run(source.command('createdb', 'account'))
                source.sql('''CREATE TABLE schema_migrations(version bigint PRIMARY KEY, dirty boolean NOT NULL);
INSERT INTO schema_migrations VALUES (2026101703,false);
CREATE TABLE users(id serial PRIMARY KEY); INSERT INTO users SELECT generate_series(1,32);
CREATE TABLE subscription_records(id serial PRIMARY KEY); INSERT INTO subscription_records VALUES (1),(2);
CREATE TABLE billing_ledger(id serial PRIMARY KEY); INSERT INTO billing_ledger VALUES (1);
CREATE SCHEMA finance; CREATE TABLE finance.invoices(id serial PRIMARY KEY);''')
                dump = source.dump
                def concurrent_dump(path, snapshot):
                    source.sql('INSERT INTO users VALUES (33);')
                    dump(path, snapshot)
                with mock.patch.object(source, 'dump', side_effect=concurrent_dump):
                    receipt = s.checkpoint(source, lambda: s.isolated_pg(image), root, contract())
                self.assertTrue(receipt['restore_verified'])
                self.assertEqual(receipt['counts']['public.users'], 32)
                self.assertEqual(source.sql('SELECT count(*) FROM users;'), '33')
                self.assertEqual(receipt['webhook_url'], declaration()['stripe']['webhook_url'])
                self.assertFalse(receipt['dirty'])
                self.assertTrue(receipt['server_version_num'].startswith('17'))
                s.private(root / 'account.dump')
                s.private(root / 'checkpoint.json')


if __name__ == '__main__':
    unittest.main()
