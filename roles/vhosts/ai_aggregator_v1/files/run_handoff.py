"""Execute one declared host using same-run IaC evidence and prepared SSH access."""
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / 'scripts'))
from ai_aggregator_handoff import validate


def require(value, message):
    if not value:
        raise ValueError(message)


def revision(root, expected):
    require(isinstance(expected, str) and re.fullmatch('[0-9a-f]{40}', expected), 'immutable source SHA required')
    actual = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    require(actual == expected, 'checkout differs from expected source SHA')
    require(not subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain', '--untracked-files=no'], text=True), 'modified source checkout')


def preflight(config):
    require(config['operation'] in ('plan', 'stage', 'activate'), 'unsupported deployment operation')
    require(config['environment'] in ('uat', 'prod'), 'explicit deployment environment required')
    require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}', config['hostname']), 'invalid target hostname')
    gitops = Path(config['gitops_root']).resolve()
    iac = Path(config['iac_root']).resolve()
    revision(gitops, config['gitops_sha'])
    revision(iac, config['iac_sha'])
    manifest = (gitops / config['service_manifest']).resolve()
    require(manifest.is_relative_to(gitops) and manifest.is_file(), 'service declaration must belong to selected GitOps checkout')
    committed = subprocess.check_output(['git', '-C', str(gitops), 'show', config['gitops_sha'] + ':' + str(manifest.relative_to(gitops))])
    require(manifest.read_bytes() == committed, 'service declaration differs from pinned GitOps source')
    data = yaml.safe_load(manifest.read_text())
    require(data.get('kind') == 'PersonalAIAggregator', 'AI Aggregator declaration required')
    require(data['metadata']['environment'] == config['environment'], 'service environment mismatch')
    require(data['spec']['entrypoint']['domain'] == config['domain'], 'service domain mismatch')
    nodes = [n for n in data['spec']['nodes'] if n.get('inventory_host') == config['hostname']]
    require(len(nodes) == 1, 'one declared deployment host required')
    if config['operation'] != 'plan':
        require(data['spec'].get('enabled') is True, 'service must be enabled')
    summary_file = Path(config['summary_file'])
    # Run the reviewed IaC owner's full verification rather than duplicating its
    # stage/target/result semantics in Playbooks. Expected bindings originate
    # from current-run workflow outputs, never from the downloaded summary.
    env = {**os.environ, 'IAC_SUMMARY': str(summary_file),
           'IAC_CHECKSUM': config['summary_sha256'], 'IAC_RUN_ID': os.environ['GITHUB_RUN_ID'],
           'IAC_ATTEMPT': os.environ['GITHUB_RUN_ATTEMPT'], 'IAC_GITOPS_SHA': config['gitops_sha'],
           'IAC_SHA': config['iac_sha'], 'IAC_MANIFEST': config['target_manifest'],
           'IAC_ACTION': 'apply', 'IAC_CORRELATION': config['correlation_id']}
    subprocess.run([sys.executable, str(iac / 'scripts/pipeline/unified_cli.py'), 'verify-summary'],
                   env=env, check=True, capture_output=True, timeout=60)
    host = validate(summary_file, config['summary_sha256'], summary_file.parent / 'inventory.json',
                    config['environment'], os.environ['GITHUB_RUN_ID'], os.environ['GITHUB_RUN_ATTEMPT'],
                    config['gitops_sha'], config['iac_sha'], config['hostname'])
    require(host.get('cloud_provider') == 'akamai-cloud', 'Akamai cloud facts required')
    require(nodes[0].get('ssh_user', host['ansible_user']) == host['ansible_user'], 'SSH user differs from declaration')
    cmdb = json.loads(Path(config['cmdb_file']).read_text())
    inventories = json.loads((summary_file.parent / 'inventory.json').read_text())
    all_hosts = {}
    for hosts in inventories.values():
        require(not set(all_hosts).intersection(hosts), 'duplicate host across IaC targets')
        all_hosts.update(hosts)
    require(cmdb == all_hosts, 'deployment CMDB differs from verified IaC facts')
    roles = set(nodes[0].get('roles', [nodes[0]['role']]))
    require(roles and roles <= {'gateway', 'cpa'}, 'unsupported AI Aggregator node role')
    require({'ai_aggregator_' + role for role in roles} <= set(host.get('groups', [])), 'declared role inventory groups required')
    return manifest, host


def execute(config):
    manifest, host = preflight(config)
    for name in ('ssh_private_key', 'known_hosts'):
        require(Path(config[name]).is_file() and Path(config[name]).stat().st_size > 0, 'prepared SSH access required')
    require(Path(config['ssh_private_key']).stat().st_mode & 0o077 == 0, 'SSH private key permissions must be private')
    require(os.environ.get('VAULT_TOKEN') if config['operation'] == 'stage' else True, 'stage requires runtime Vault identity')
    with tempfile.TemporaryDirectory(prefix='ai-aggregator-deploy-') as temp:
        variables = {'ai_aggregator_operation': config['operation'], 'ai_aggregator_manifest_file': str(manifest)}
        if config['operation'] == 'stage':
            bundle = Path(config['apisix_bundle_dir']).resolve()
            require(all((bundle / name).is_file() for name in ('config.yaml', 'apisix.yaml')), 'rendered APISIX bundle required')
            variables['ai_aggregator_apisix_bundle_dir'] = str(bundle)
        extra = Path(temp) / 'vars.json'
        extra.write_text(json.dumps(variables))
        extra.chmod(0o600)
        env = {**os.environ, 'AI_WORKSPACE_CMDB_JSON': str(Path(config['cmdb_file']).resolve()),
               'ANSIBLE_HOST_KEY_CHECKING': 'True',
               'ANSIBLE_SSH_ARGS': '-o StrictHostKeyChecking=yes -o GlobalKnownHostsFile=/dev/null -o UserKnownHostsFile=' + shlex.quote(str(Path(config['known_hosts']).resolve()))}
        # No shell interpolation, static inventory, IP override or credential
        # fallback. Exact CMDB facts remain owned by IaC.
        effective = json.loads(subprocess.check_output(
            ['ansible-inventory', '-i', str(ROOT / 'inventory/terraform_cmdb.py'), '--list'], env=env, text=True, timeout=60))
        effective_host = effective.get('_meta', {}).get('hostvars', {}).get(config['hostname'], {})
        require(effective_host.get('ansible_host') == host['ip'] and effective_host.get('ansible_user') == host['ansible_user'],
                'effective Ansible target differs from verified cloud facts')
        subprocess.run(['ansible-playbook', '-i', str(ROOT / 'inventory/terraform_cmdb.py'),
                        '--limit', config['hostname'], '--private-key', str(Path(config['ssh_private_key']).resolve()),
                        str(ROOT / 'deploy-ai-aggregator-v1.yml'), '-e', '@' + str(extra)],
                       cwd=ROOT, env=env, check=True, timeout=3600)
    return {'schema_version': 1, 'result': 'playbook_completed', 'operation': config['operation'],
            'environment': config['environment'], 'hostname': config['hostname'], 'domain': config['domain'],
            'instance_id': host['instance_id'], 'gitops_sha': config['gitops_sha'], 'iac_sha': config['iac_sha'],
            'summary_sha256': config['summary_sha256'], 'run_id': os.environ['GITHUB_RUN_ID'],
            'run_attempt': os.environ['GITHUB_RUN_ATTEMPT']}


if __name__ == '__main__':
    try:
        destination = Path(os.environ['AI_AGGREGATOR_RECEIPT'])
        destination.unlink(missing_ok=True)
        result = execute(json.loads(os.environ['AI_AGGREGATOR_HANDOFF']))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(result, sort_keys=True) + '\n')
    except Exception as error:
        print('AI Aggregator deployment rejected: ' + type(error).__name__, file=sys.stderr)
        sys.exit(1)
