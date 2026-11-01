"""Validate the deployment target against an authenticated IaC summary."""
import hashlib
import ipaddress
import json
from pathlib import Path


def validate(summary_path, checksum, inventory_path, environment, run_id, attempt,
             gitops_sha, iac_sha, hostname):
    raw = Path(summary_path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != checksum:
        raise ValueError('IaC summary checksum mismatch')
    summary = json.loads(raw)
    context = summary['context']
    for key, expected in {'environment': environment, 'run_id': str(run_id),
                          'run_attempt': str(attempt), 'gitops_sha': gitops_sha,
                          'iac_sha': iac_sha, 'action': 'apply'}.items():
        if context.get(key) != expected:
            raise ValueError('IaC context mismatch: ' + key)
    if summary.get('result') != 'applied_verified':
        raise ValueError('verified IaC apply required')
    inventories = json.loads(Path(inventory_path).read_bytes())
    receipts = [r for r in summary['receipts'] if r.get('inventory_sha256')]
    if not receipts or set(inventories) != {r['target_id'] for r in receipts}:
        raise ValueError('inventory target set differs from IaC receipts')
    for receipt in receipts:
        encoded = (json.dumps(inventories[receipt['target_id']], sort_keys=True, indent=2) + '\n').encode()
        if hashlib.sha256(encoded).hexdigest() != receipt['inventory_sha256']:
            raise ValueError('IaC inventory checksum mismatch')
    matches = [facts for hosts in inventories.values() for name, facts in hosts.items()
               if name == hostname]
    if len(matches) != 1:
        raise ValueError('expected exactly one IaC deployment host')
    host = matches[0]
    if not host.get('instance_id') or not host.get('ansible_user'):
        raise ValueError('IaC host identity and SSH user required')
    ipaddress.ip_address(host['ip'])
    return host
