#!/usr/bin/env python3
"""Execute the cold-generation owner roles from one receipt-verified CMDB."""
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import yaml

ROOT = Path(__file__).resolve().parents[1]
RESOURCE = 'resources/svc.plus/prod/akamai/prod-k3s-v6.yaml'


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def prepare(cmdb, declaration, gitops_sha, checkout, receipt):
    g = declaration['global']
    require((g['environment'], g['provider'], g['account'], g['workspace']) ==
            ('prod', 'akamai-cloud', 'manbuzhe2026', 'prod-k3s-v6'), 'Wrong declaration scope')
    require(len(declaration['hosts']) == 1, 'Expected one declared node')
    host = declaration['hosts'][0]
    name = host['name']
    require(name == 'prod-k3s-v6' and host['type'] == 'g6-standard-2', 'Wrong candidate or plan')
    records = cmdb.get('hosts', {})
    if not records:
        records = {k: v for k, v in cmdb.items() if isinstance(v, dict) and ('ip' in v or 'ansible_host' in v)}
    require(list(records) == [name], 'CMDB must contain only the reviewed candidate')
    live = records[name]
    require('prod' in live.get('groups', []) and 'k3s' in live.get('groups', []), 'CMDB environment or runtime mismatch')
    require((live.get('cloud_provider') or live.get('host_vars', {}).get('cloud_provider')) == g['provider'], 'CMDB provider mismatch')
    require(live.get('type') == host['type'] and live.get('image') == host['image'], 'CMDB plan or image mismatch')
    require(live.get('region') == host['region'], 'CMDB region mismatch')
    node_id = str(live.get('instance_id', ''))
    require(re.fullmatch(r'[1-9][0-9]*', node_id), 'Missing provider instance ID')
    address = live.get('ip') or live.get('ansible_host')
    require(ipaddress.ip_address(address).version == 4 and ipaddress.ip_address(address).is_global, 'Invalid candidate IP')
    volume = live.get('data_volume', {})
    require(str(volume.get('linode_id')) == node_id, 'Volume belongs to another instance')
    require(re.fullmatch(r'[1-9][0-9]*', str(volume.get('volume_id', ''))), 'Missing volume ID')
    require(volume.get('label') == host['volume']['label'] and int(volume.get('size_gb', 0)) >= host['volume']['size'], 'Wrong data volume label or size')
    require(volume.get('region') == host['region'], 'Wrong data volume region')
    device = volume.get('filesystem_path', '')
    require(re.fullmatch(r'/dev/disk/by-id/[A-Za-z0-9._+-]+', device), 'Invalid stable block device')
    declared = host['host_vars']
    # Only reviewed bootstrap variables enter the dynamic inventory; CMDB extras
    # cannot replace connection plugins, commands, role variables, or topology.
    vars_ = {k: v for k, v in declared.items() if k.startswith('k3s_platform_')}
    vars_.update(target_generation=declared['target_generation'],
                 k3s_platform_git_commit=gitops_sha, k3s_platform_gitops_checkout_path=str(checkout),
                 k3s_platform_receipt_path=str(receipt), k3s_platform_git_auth_mode='public',
                 web_saas_data_environment='prod', web_saas_data_target_host=name,
                 web_saas_data_action='prepare_empty', web_saas_data_allow_initialize=True,
                 web_saas_data_allow_generation_host=True,
                 web_saas_data_expected_volume_id=volume['volume_id'],
                 web_saas_data_device_path=device, web_saas_data_filesystem_label=host['volume']['label'])
    clean = {'schema_version': 'cmdb.v1', 'environment': 'prod', 'account': g['account'],
             'provider': g['provider'], 'hosts': {name: {
                 'ip': address, 'instance_id': node_id, 'region': host['region'],
                 'groups': host['groups'], 'ansible_user': 'root', 'data_volume': volume,
                 'host_vars': vars_}}}
    return name, clean


def execute(argv, env, **kwargs):
    return subprocess.run(argv, check=True, env=env, **kwargs)


def main():
    p = argparse.ArgumentParser()
    for key in ('cmdb', 'cmdb-sha256', 'gitops', 'gitops-sha', 'known-hosts', 'receipt'):
        p.add_argument('--' + key, required=True)
    a = p.parse_args()
    checkout = Path(a.gitops).resolve()
    require(re.fullmatch(r'[0-9a-f]{40}', a.gitops_sha), 'Full GitOps SHA required')
    require(subprocess.check_output(['git', '-C', str(checkout), 'rev-parse', 'HEAD'], text=True).strip() == a.gitops_sha, 'GitOps checkout mismatch')
    require(not subprocess.check_output(['git', '-C', str(checkout), 'status', '--porcelain'], text=True).strip(), 'Dirty GitOps checkout')
    raw = Path(a.cmdb).read_bytes()
    require(hashlib.sha256(raw).hexdigest() == a.cmdb_sha256, 'CMDB checksum mismatch')
    name, clean = prepare(json.loads(raw), yaml.safe_load((checkout / RESOURCE).read_text()), a.gitops_sha, checkout, Path(a.receipt).resolve())
    known = Path(a.known_hosts).resolve()
    require(known.is_file() and known.stat().st_size > 0, 'Provider-attested known_hosts required')
    # Render/validate the complete paused graph before SSH or disk changes.
    execute(['python3', str(ROOT / 'roles/vhosts/k3s_platform_bootstrap/files/stage_flux_declarations.py'),
             '--checkout', str(checkout), '--commit', a.gitops_sha,
             '--root-path', './environments/clusters/prod-k3s-v6', '--root-name', name,
             '--source', 'config-prod', '--namespace', 'flux-system',
             '--git-url', 'https://github.com/ai-workspace-infra/gitops.git'], os.environ.copy(), stdout=subprocess.DEVNULL)
    env = {k: v for k, v in os.environ.items() if not k.startswith(('VAULT_', 'LINODE_'))}
    key = env.pop('SSH_PRIVATE_KEY', '')
    require(bool(key), 'Runtime Vault SSH key missing')
    with tempfile.TemporaryDirectory(prefix='v6-bootstrap-') as tmp:
        os.chmod(tmp, 0o700)
        cmdb = Path(tmp) / 'cmdb.json'
        cmdb.write_text(json.dumps(clean))
        cmdb.chmod(0o600)
        env.update(AI_WORKSPACE_CMDB_JSON=str(cmdb), ANSIBLE_ROLES_PATH=str(ROOT / 'roles'),
                   ANSIBLE_HOST_KEY_CHECKING='True', ANSIBLE_CONFIG=str(ROOT / 'ansible.cfg'),
                   ANSIBLE_SSH_ARGS='-o ControlMaster=auto -o ControlPersist=60s -o StrictHostKeyChecking=yes -o UserKnownHostsFile=' + shlex.quote(str(known)) + ' -o BatchMode=yes -o PasswordAuthentication=no')
        agent = subprocess.check_output(['ssh-agent', '-s'], text=True, env=env)
        for field in ('SSH_AUTH_SOCK', 'SSH_AGENT_PID'):
            env[field] = re.search(field + r'=([^;]+);', agent).group(1)
        try:
            execute(['ssh-add', '-'], env, input=key, text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            key = ''
            inv = ['-i', str(ROOT / 'inventory/terraform_cmdb.py')]
            # Inventory output contains only reviewed non-secret metadata.
            execute(['ansible-inventory', *inv, '--host', name], env)
            execute(['ansible-inventory', *inv, '--graph'], env)
            playbook = str(ROOT / 'prod_k3s_generation_bootstrap.yml')
            execute(['ansible-playbook', *inv, playbook, '--syntax-check'], env)
            execute(['ansible', *inv, name, '-m', 'ping'], env)
            execute(['ansible-playbook', *inv, playbook, '--limit', name], env)
        finally:
            execute(['ssh-agent', '-k'], env, stdout=subprocess.DEVNULL)
    receipt = json.loads(Path(a.receipt).read_text())
    require(receipt.get('result') == 'PAUSED_BOOTSTRAP_VERIFIED' and receipt.get('gitops_sha') == a.gitops_sha, 'Missing verified paused receipt')
    require(receipt.get('counts') == {'GitRepository': 6, 'Kustomization': 6, 'HelmRelease': 7}, 'Unexpected V6 release graph')
    require(receipt.get('common', {}).get('result') == 'COMMON_NODE_VERIFIED', 'Vhost common identity checks not verified')
    require(receipt.get('audit', {}).get('result') == 'AUDIT_ALLOW_DENY_VERIFIED', 'Independent audit identity not verified')
    print('PAUSED_BOOTSTRAP_VERIFIED; no business reconciliation or writer transfer performed')


if __name__ == '__main__':
    main()
