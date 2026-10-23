#!/usr/bin/env python3
"""Explicit candidate-only migration listener dispatcher; no source deployment."""
import argparse
import base64
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
import entrypoint as owner

RUNTIME = '/etc/xcontrol/migration-api'
KEYS = ('TARGET_MIGRATION_DSN', 'TARGET_MIGRATION_SIGNING_KEY', 'TARGET_MIGRATION_STAGING_KEY')


def validate(document, contract, gitops):
    owner.fields(document, ['schema_version', 'kind', 'control_plane_config', 'listener', 'doco_target'], ['example_only'])
    reject_example(document)
    owner.require(type(document['schema_version']) is int and document['schema_version'] == 1 and document['kind'] == 'selfhost-migration-listener'
                  and contract['execution']['mode'] == 'candidate', 'Explicit candidate listener declaration required')
    cfg = document['listener']
    owner.require(document['doco_target'] != contract['release']['doco_cd']['target'], 'Listener requires a separate explicit opt-in selection')
    owner.fields(cfg, ['source_url', 'source_audience', 'target_audience', 'job_id', 'allowed_peer_cidrs',
                       'allowed_client_cidrs', 'apply_enabled', 'initialize_receipts', 'receipt_owner_role'],
                 ['receipt_file'])
    owner.require(owner.matches(r'https://[a-z][a-z0-9-]*(?:\.[a-z0-9][a-z0-9-]*)+', cfg['source_url']) and
                  cfg['source_url'] != 'https://' + contract['target']['accounts_domain'], 'Exact distinct HTTPS source required')
    owner.require(str(uuid.UUID(cfg['job_id'])) == cfg['job_id'] and uuid.UUID(cfg['job_id']).int != 0, 'Canonical migration job UUID required')
    for key in ('source_audience', 'target_audience', 'receipt_owner_role'):
        owner.require(owner.matches(r'[a-z][a-z0-9_-]{0,62}', cfg[key]), 'Unsafe migration identity')
    owner.require(cfg['source_audience'] != cfg['target_audience'] and
                  cfg['receipt_owner_role'] not in ('postgres', 'account_user', 'readonly_release'), 'Dedicated target migration identity required')
    for key in ('allowed_peer_cidrs', 'allowed_client_cidrs'):
        owner.require(isinstance(cfg[key], list) and cfg[key], 'Explicit exact migration networks required')
        for value in cfg[key]:
            network = ipaddress.ip_network(value, strict=True)
            owner.require(network.prefixlen >= (24 if network.version == 4 else 64) and
                          not network.is_unspecified and not network.is_multicast, 'Broad or unsafe migration network')
    owner.require('127.0.0.1/32' in cfg['allowed_client_cidrs'], 'Explicit loopback client CIDR required for namespace-local SNI verification')
    owner.require(type(cfg['apply_enabled']) is bool and type(cfg['initialize_receipts']) is bool and
                  (not cfg['initialize_receipts'] or cfg['apply_enabled']), 'Receipt DDL requires explicit apply eligibility')
    owner.require(('receipt_file' in cfg) == cfg['apply_enabled'], 'Apply requires a target-only backup receipt path')
    if cfg['apply_enabled']:
        owner.require(cfg['receipt_file'].startswith(('/run/', '/data/')) and '..' not in Path(cfg['receipt_file']).parts,
                      'Canonical target-only backup receipt required')
    selected = owner.selection(gitops, document['doco_target'], contract['release']['services'], listener=True)
    services = selected.pop('services')
    owner.require('migration' in services and 'caddy' in services, 'GitOps must declare listener and proxy')
    service = services['migration']
    owner.fields(service, ['image', 'container_name', 'entrypoint', 'command', 'network_mode', 'env_file',
                           'read_only', 'user', 'cap_drop', 'security_opt', 'volumes'], ['restart', 'depends_on'])
    owner.require(service.get('image') == contract['release']['services']['accounts'] and
                  service.get('container_name') == 'web-saas-migration-listener' and
                  service.get('entrypoint') == ['/usr/local/bin/migratectl'] and
                  service.get('command') == arguments(cfg) and service.get('network_mode') == 'service:caddy' and
                  not service.get('ports') and not service.get('networks') and not service.get('environment') and
                  service.get('env_file') == [RUNTIME + '/listener.env'] and service.get('read_only') is True and
                  service.get('user') == '0:0' and service.get('cap_drop') == ['ALL'] and
                  service.get('security_opt') == ['no-new-privileges:true'], 'GitOps listener runtime differs from reviewed CLI contract')
    stage = str(Path(contract['resolved']['data_disk']['data_path']).parent / 'migration-stage')
    expected = [{'type': 'bind', 'source': stage, 'target': '/private/target-stage', 'bind': {'create_host_path': False}}]
    if cfg['apply_enabled']:
        path = cfg['receipt_file']
        expected.append({'type': 'bind', 'source': path, 'target': path, 'read_only': True, 'bind': {'create_host_path': False}})
    owner.require(service.get('volumes') == expected, 'Listener may mount only private staging and target backup receipt')
    route = {'type': 'bind', 'source': RUNTIME + '/route.caddy', 'target': '/etc/caddy/migration-listener.caddy',
             'read_only': True, 'bind': {'create_host_path': False}}
    owner.require(route in services['caddy'].get('volumes', []) and services['caddy'].get('container_name') == 'web-saas-caddy',
                  'GitOps must explicitly mount the candidate migration route')
    return {'declaration': document, 'contract': contract, 'stage': stage}


def reject_example(document):
    owner.require(type(document.get('example_only', False)) is bool and document.get('example_only', False) is False,
                  'Example-only listener declarations are never executable')


def validate_recovery_input(recovery, receipt_file):
    owner.fields(recovery, ['backup_receipt', 'backup_receipt_sha256', 'owner_proof', 'owner_proof_sha256',
                            'owner_sha', 'plan_id', 'plan_sha256', 'backup_archive'])
    owner.require(recovery['backup_receipt'] == receipt_file, 'Target receipt path differs from declaration')
    for key in ('backup_receipt', 'owner_proof', 'backup_archive'):
        path = Path(recovery[key])
        owner.require(path.is_absolute() and '..' not in path.parts and str(path).startswith(('/run/', '/data/')),
                      'Recovery evidence must be canonical target-only owner output')
    for key in ('backup_receipt_sha256', 'owner_proof_sha256', 'plan_sha256'):
        owner.require(owner.matches(owner.HASH, recovery[key]), 'Recovery checksum required')
    owner.require(owner.matches(owner.SHA, recovery['owner_sha']) and str(uuid.UUID(recovery['plan_id'])) == recovery['plan_id'],
                  'Recovery owner pin and original plan ID required')


def arguments(cfg):
    args = ['serve', '--role', 'target', '--audience', cfg['target_audience'], '--source-audience', cfg['source_audience'],
            '--target-audience', cfg['target_audience'], '--job-id', cfg['job_id'], '--dsn-env', KEYS[0],
            '--auth-key-env', KEYS[1], '--staging-key-env', KEYS[2], '--staging-dir', '/private/target-stage',
            '--allow-loopback-http', '--allowed-source-url', cfg['source_url']]
    for network in cfg['allowed_peer_cidrs']:
        args += ['--allowed-peer-cidr', network]
    if cfg['apply_enabled']:
        args += ['--backup-receipt-file', cfg['receipt_file']]
    if cfg['initialize_receipts']:
        args += ['--initialize-receipts', '--receipt-owner-role', cfg['receipt_owner_role']]
    return args


def resolve(policy, environment):
    owner.fields(policy, ['vault_addr', 'vault_token_env', 'secret_refs'])
    owner.require(owner.matches(r'https://[A-Za-z0-9.:-]+', policy['vault_addr']) and
                  owner.matches(r'[A-Z][A-Z0-9_]*', policy['vault_token_env']) and os.environ.get(policy['vault_token_env']), 'Explicit Vault identity required')
    owner.fields(policy['secret_refs'], KEYS)
    result = {}
    env = {k: v for k, v in os.environ.items() if k != 'VAULT_AGENT_ADDR'}
    env.update(VAULT_ADDR=policy['vault_addr'], VAULT_TOKEN=os.environ[policy['vault_token_env']])
    for key, reference in policy['secret_refs'].items():
        owner.require(owner.matches('kv/' + environment + r'/accounts/migration/[a-z0-9_-]+#[A-Za-z0-9_]+', reference), 'Migration-only scoped Vault references required')
        path, field = reference.split('#')
        response = subprocess.run(['vault', 'kv', 'get', '-format=json', path], env=env, capture_output=True, text=True, timeout=30)
        owner.require(response.returncode == 0, 'Migration Vault record unavailable')
        value = json.loads(response.stdout)['data']['data'][field]
        owner.require(isinstance(value, str) and value and not any(c in value for c in '\r\n\x00$'), 'Unsafe migration Vault value; DSN passwords must be URI encoded')
        result[key] = value
    for key in KEYS[1:]:
        owner.require(len(base64.b64decode(result[key], validate=True)) == 32, 'Dedicated 32-byte migration keys required')
    owner.require(result[KEYS[1]] != result[KEYS[2]], 'Signing and staging keys must differ')
    from urllib.parse import urlsplit
    dsn = urlsplit(result[KEYS[0]])
    owner.require(dsn.scheme in ('postgres', 'postgresql') and dsn.username not in (None, 'postgres', 'account_user', 'readonly_release'), 'Dedicated migration executor DSN required')
    return result


def main():
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument('--config', required=True)
    parser.add_argument('--gitops-ref', required=True)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    root, playbooks = (Path(os.environ[key]).resolve(strict=True) for key in ('GITOPS_CHECKOUT', 'PLAYBOOKS_CHECKOUT'))
    owner.require(Path(__file__).resolve() == playbooks / 'scripts/control_plane/listener.py', 'Listener must belong to pinned owner')
    document = owner.read_yaml(owner.inside(root, root, args.config))
    reject_example(document)
    contract, _ = owner.load_contract(document['control_plane_config'], root, playbooks, args.gitops_ref)
    validated = validate(document, contract, root)
    if document['listener']['apply_enabled']:
        recovery = json.loads(owner.protected(os.environ['CONTROL_PLANE_MIGRATION_RECOVERY_FILE']).read_text())
        validate_recovery_input(recovery, document['listener']['receipt_file'])
        validated['recovery'] = recovery
    policy = owner.read_yaml(owner.protected(os.environ['CONTROL_PLANE_MIGRATION_VARS_FILE']))
    owner.fields(policy, ['vault_addr', 'vault_token_env', 'secret_refs'])
    owner.fields(policy['secret_refs'], KEYS)
    for reference in policy['secret_refs'].values():
        owner.require(owner.matches('kv/' + contract['environment'] + r'/accounts/migration/[a-z0-9_-]+#[A-Za-z0-9_]+', reference), 'Reference-only migration policy required')
    if args.check:
        print(json.dumps({'status': 'validated_listener_plan', 'host_actions': False, 'source_actions': False}))
        return
    # Resolution is delegated by Ansible; no controller credential file is made.
    env = {k: v for k, v in os.environ.items() if not k.startswith('ANSIBLE_')}
    env.update(ANSIBLE_CONFIG=str(playbooks / 'scripts/control_plane/ansible.cfg'), ANSIBLE_ROLES_PATH=str(playbooks / 'roles'),
               AI_WORKSPACE_CMDB_JSON=contract['resolved']['cmdb_file'], CMDB_FILE=contract['resolved']['cmdb_file'], PYTHONDONTWRITEBYTECODE='1')
    variables = json.dumps({'migration_listener': validated, 'migration_listener_policy': policy,
                            'migration_listener_arguments': arguments(document['listener'])})
    inventory, host = str(playbooks / 'inventory/terraform_cmdb.py'), contract['target']['host']
    for argv, data in [(['ansible-inventory', '-i', inventory, '--host', host], None),
                       (['ansible-playbook', '-i', inventory, str(playbooks / 'selfhost-migration-listener.yml'), '--limit', host, '--syntax-check', '-e', '@/dev/stdin'], variables),
                       (['ansible', '-i', inventory, host, '-m', 'ansible.builtin.ping'], None),
                       (['ansible-playbook', '-i', inventory, str(playbooks / 'selfhost-migration-listener.yml'), '--limit', host, '-e', '@/dev/stdin'], variables)]:
        result = subprocess.run(argv, cwd=playbooks, env=env, input=data, text=True, capture_output=True)
        owner.require(result.returncode == 0, 'Listener owner failed; private output withheld')
    owner.require('MIGRATION_LISTENER_VERIFIED' in result.stdout, 'Listener acceptance receipt absent')
    print(json.dumps({'status': 'candidate_listener_verified', 'source_actions': False, 'business_acceptance': False}))


if __name__ == '__main__':
    try:
        if os.environ.get('CONTROL_PLANE_LISTENER_RESOLVE') == '1':
            request = json.load(sys.stdin)
            print(json.dumps(resolve(request['policy'], request['environment'])))
        else:
            main()
    except Exception:
        print('Candidate migration listener refused; private values withheld.', file=sys.stderr)
        sys.exit(2)
