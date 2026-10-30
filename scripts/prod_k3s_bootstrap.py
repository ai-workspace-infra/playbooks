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


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def prepare(cmdb, declaration, gitops_sha, checkout, receipt, phase="cluster"):
    require(phase in {'common', 'cluster'}, 'Unsupported bootstrap phase')
    g = declaration['global']
    require(g['environment'] in {'sit', 'uat', 'prod'} and g['provider'] == 'akamai-cloud'
            and g['account'] and g['workspace'], 'Unsupported declaration scope')
    require(len(declaration['hosts']) == 1, 'Expected one declared node')
    host = declaration['hosts'][0]
    name = host['name']
    require(re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}', name), 'Invalid declared hostname')
    records = cmdb.get('hosts', {})
    if not records:
        records = {k: v for k, v in cmdb.items() if isinstance(v, dict) and ('ip' in v or 'ansible_host' in v)}
    require(list(records) == [name], 'CMDB must contain only the reviewed candidate')
    live = records[name]
    require(g['environment'] in live.get('groups', []) and 'k3s' in live.get('groups', []), 'CMDB environment or runtime mismatch')
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
    vars_.update(prod_k3s_bootstrap_phase=phase, target_generation=declared['target_generation'],
                 k3s_platform_git_commit=gitops_sha, k3s_platform_gitops_checkout_path=str(checkout),
                 k3s_platform_receipt_path=str(receipt), k3s_platform_git_auth_mode='public',
                 web_saas_data_environment=g['environment'], web_saas_data_target_host=name,
                 web_saas_data_action='prepare_empty' if phase == 'cluster' else 'inspect', web_saas_data_allow_initialize=phase == 'cluster',
                 web_saas_data_allow_generation_host=True,
                 web_saas_data_expected_volume_id=volume['volume_id'],
                 web_saas_data_device_path=device, web_saas_data_filesystem_label=host['volume']['label'])
    clean = {'schema_version': 'cmdb.v1', 'environment': g['environment'], 'account': g['account'],
             'provider': g['provider'], 'hosts': {name: {
                 'ip': address, 'instance_id': node_id, 'region': host['region'],
                 'groups': host['groups'], 'ansible_user': 'root', 'data_volume': volume,
                 'host_vars': vars_}}}
    return name, clean


def execute(argv, env, **kwargs):
    return subprocess.run(argv, check=True, env=env, **kwargs)


def main():
    p = argparse.ArgumentParser()
    for key in ('cmdb', 'cmdb-sha256', 'gitops', 'gitops-sha', 'known-hosts', 'receipt', 'resource-manifest'):
        p.add_argument('--' + key, required=True)
    p.add_argument('--phase', choices=('common', 'cluster'), default='cluster')
    a = p.parse_args()
    checkout = Path(a.gitops).resolve()
    require(re.fullmatch(r'[0-9a-f]{40}', a.gitops_sha), 'Full GitOps SHA required')
    require(subprocess.check_output(['git', '-C', str(checkout), 'rev-parse', 'HEAD'], text=True).strip() == a.gitops_sha, 'GitOps checkout mismatch')
    require(not subprocess.check_output(['git', '-C', str(checkout), 'status', '--porcelain'], text=True).strip(), 'Dirty GitOps checkout')
    raw = Path(a.cmdb).read_bytes()
    require(hashlib.sha256(raw).hexdigest() == a.cmdb_sha256, 'CMDB checksum mismatch')
    manifest = (checkout / a.resource_manifest).resolve()
    require(manifest.is_relative_to(checkout) and manifest.is_file(), 'Invalid resource manifest path')
    name, clean = prepare(json.loads(raw), yaml.safe_load(manifest.read_text()), a.gitops_sha, checkout, Path(a.receipt).resolve(), phase=a.phase)
    known = Path(a.known_hosts).resolve()
    require(known.is_file() and known.stat().st_size > 0, 'Provider-attested known_hosts required')
    declared = clean['hosts'][name]['host_vars']
    # Resolve the stable root and batch path independently from the hostname.
    execute(['python3', str(ROOT / 'roles/vhosts/k3s_platform_bootstrap/files/stage_flux_declarations.py'),
             '--checkout', str(checkout), '--commit', a.gitops_sha,
             '--root-path', declared['k3s_platform_flux_root_path'],
             '--root-name', declared['k3s_platform_flux_root_name'],
             '--source', declared['k3s_platform_flux_source_name'],
             '--namespace', declared.get('k3s_platform_flux_namespace', 'flux-system'),
             '--git-url', declared['k3s_platform_git_url'],
             '--state', 'active' if declared['k3s_platform_flux_bootstrap_mode'] == 'declarative_active' else 'paused'], os.environ.copy(), stdout=subprocess.DEVNULL)
    env = {k: v for k, v in os.environ.items() if not k.startswith(('VAULT_', 'LINODE_'))}
    key = env.pop('SSH_PRIVATE_KEY', '')
    require(bool(key), 'Runtime Vault SSH key missing')
    with tempfile.TemporaryDirectory(prefix='k3s-bootstrap-') as tmp:
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
    require(receipt.get('gitops_sha') == a.gitops_sha, 'Receipt source binding mismatch')
    common = receipt if a.phase == 'common' else receipt.get('common', {})
    require(common.get('result') == 'COMMON_NODE_VERIFIED', 'Vhost common identity checks not verified')
    require(common.get('node') == name and str(common.get('cmdb_instance_id')) == str(clean['hosts'][name]['instance_id']), 'Common receipt node binding differs')
    if a.phase == 'common':
        require(receipt.get('result') == 'COMMON_NODE_VERIFIED', 'Missing common-only receipt')
        print('COMMON_NODE_VERIFIED; host identity preparation complete')
        return
    require(receipt.get('result') == ('FLUX_HANDOVER_VERIFIED' if declared['k3s_platform_flux_bootstrap_mode'] == 'declarative_active' else 'PAUSED_BOOTSTRAP_VERIFIED'), 'Missing verified bootstrap receipt')
    require(receipt.get('audit', {}).get('result') == 'AUDIT_ALLOW_DENY_VERIFIED', 'Independent audit identity not verified')
    print(receipt['result'])


if __name__ == '__main__':
    main()
