"""Collect V6 JWT trust material only from the pinned authenticated K3s cluster.

The caller owns kubeconfig provenance and must compare the supplied UIDs with an
independently verified bootstrap receipt. Values returned here include secrets
and must remain in memory; they must never be printed or saved in receipts.
"""
import json
import re
import subprocess

from signing_keys import discovery_contract

NODE = 'prod-k3s-v6'
NAMESPACE = 'vault-prod'
SA = 'vault-tls-reader'
AUDIENCE = 'vault-prod-k3s-v6-eso'
DENIED_AUDIENCE = 'vault-prod-k3s-v6-denied-probe'


class ClusterError(Exception):
    pass


def command(*args):
    try:
        result = subprocess.run(['kubectl', *args], text=True, capture_output=True,
                                timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise ClusterError('authenticated cluster operation failed') from None
    if result.returncode:
        # kubectl diagnostics can contain sensitive inputs; never forward them.
        raise ClusterError('authenticated cluster operation failed')
    return result.stdout.strip()


def collect(cluster_uid, node_uid, expected_version, run=command):
    """Return discovery and three TokenRequest values after target verification."""
    if not all(isinstance(x, str) and re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', x)
               for x in (cluster_uid, node_uid)):
        raise ClusterError('trusted cluster and node UIDs are required')
    if expected_version != 'v1.37.1+k3s1':
        raise ClusterError('unexpected K3s version')
    try:
        ns = json.loads(run('get', 'namespace', 'kube-system', '-o', 'json'))
        nodes = json.loads(run('get', 'nodes', '-o', 'json'))['items']
        if ns['metadata']['uid'] != cluster_uid or len(nodes) != 1:
            raise ClusterError('cluster identity differs from receipt')
        node = nodes[0]
        if (node['metadata']['name'] != NODE or node['metadata']['uid'] != node_uid
                or node['status']['nodeInfo']['kubeletVersion'] != expected_version
                or not any(x['type'] == 'Ready' and x['status'] == 'True' for x in node['status']['conditions'])):
            raise ClusterError('node identity or readiness differs from receipt')
        for name in (SA, 'default'):
            account = json.loads(run('get', 'serviceaccount', name, '-n', NAMESPACE, '-o', 'json'))
            if account['metadata']['name'] != name or account['metadata']['namespace'] != NAMESPACE:
                raise ClusterError('service account identity differs')
        issuer, pems = discovery_contract(run('get', '--raw', '/.well-known/openid-configuration'),
                                           run('get', '--raw', '/openid/v1/jwks'))
        def token(name, audience):
            value = run('create', 'token', name, '-n', NAMESPACE, '--audience', audience, '--duration', '600s')
            if not value or value.count('.') != 2 or any(c.isspace() for c in value):
                raise ClusterError('invalid service account TokenRequest result')
            return value
        return {'issuer': issuer, 'public_keys': pems,
                'good_jwt': token(SA, AUDIENCE),
                'wrong_audience_jwt': token(SA, DENIED_AUDIENCE),
                'wrong_service_account_jwt': token('default', AUDIENCE)}
    except (KeyError, TypeError, json.JSONDecodeError, ValueError):
        raise ClusterError('cluster identity discovery failed') from None
