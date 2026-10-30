"""Collect V6 JWT trust material only from the pinned authenticated K3s cluster.

The caller owns kubeconfig provenance and must compare the supplied UIDs with an
independently verified bootstrap receipt. Values returned here include secrets
and must remain in memory; they must never be printed or saved in receipts.
"""
import json
import re
import subprocess

from signing_keys import discovery_contract




class ClusterError(Exception):
    def __init__(self, message='authenticated cluster operation failed', stage='cluster_identity', diagnostic=None):
        super().__init__(message)
        self.stage = stage
        self.diagnostic = diagnostic


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


def collect(cluster_uid, node_uid, expected_version, node_name, identity, run=command):
    """Return discovery and three TokenRequest values after target verification."""
    if not all(isinstance(x, str) and re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', x)
               for x in (cluster_uid, node_uid)):
        raise ClusterError('trusted cluster and node UIDs are required')
    if not isinstance(expected_version,str) or not re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+[+A-Za-z0-9.-]*',expected_version):
        raise ClusterError('explicit Kubernetes version required')
    if not isinstance(node_name,str) or not re.fullmatch(r'[a-z0-9][a-z0-9.-]*',node_name):
        raise ClusterError('invalid expected node name')
    stage = 'namespace_read'
    try:
        ns = json.loads(run('get', 'namespace', 'kube-system', '-o', 'json'))
        stage = 'nodes_read'
        nodes = json.loads(run('get', 'nodes', '-o', 'json'))['items']
        if ns['metadata']['uid'] != cluster_uid:
            raise ClusterError('cluster identity differs from receipt', 'cluster_identity_mismatch')
        stage = 'node_receipt_match'
        selected = [node for node in nodes if node.get('metadata', {}).get('name') == node_name]
        if len(selected) != 1:
            raise ClusterError('expected node not uniquely present', stage)
        node = selected[0]
        if (node['metadata']['name'] != node_name or node['metadata']['uid'] != node_uid
                or node['status']['nodeInfo']['kubeletVersion'] != expected_version
                or not any(x['type'] == 'Ready' and x['status'] == 'True' for x in node['status']['conditions'])):
            raise ClusterError('node identity or readiness differs from receipt', stage)
        stage = 'service_accounts_read'
        for name in (identity.service_account, identity.probe_service_account):
            account = json.loads(run('get', 'serviceaccount', name, '-n', identity.namespace, '-o', 'json'))
            if account['metadata']['name'] != name or account['metadata']['namespace'] != identity.namespace:
                raise ClusterError('service account identity differs', stage)
        stage = 'oidc_discovery'
        issuer, pems = discovery_contract(run('get', '--raw', '/.well-known/openid-configuration'),
                                           run('get', '--raw', '/openid/v1/jwks'))
        def token(name, audience):
            nonlocal stage
            stage = 'token_requests'
            value = run('create', 'token', name, '-n', identity.namespace, '--audience', audience, '--duration', '600s')
            if not value or value.count('.') != 2 or any(c.isspace() for c in value):
                raise ClusterError('invalid service account TokenRequest result')
            return value
        return {'issuer': issuer, 'public_keys': pems,
                'good_jwt': token(identity.service_account, identity.audience),
                'wrong_audience_jwt': token(identity.service_account, identity.denied_audience),
                'wrong_service_account_jwt': token(identity.probe_service_account, identity.audience)}
    except ClusterError as exc:
        if exc.stage == 'cluster_identity':
            raise ClusterError(stage, stage) from None
        raise
    except (KeyError, TypeError, json.JSONDecodeError, ValueError):
        raise ClusterError(stage, stage) from None


def verify_cluster_target(cluster_uid, node_uid, expected_version, node_name, run):
    """Verify only the bootstrap-bound cluster and node before staged Flux work."""
    if not all(isinstance(x, str) and re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', x)
               for x in (cluster_uid, node_uid)):
        raise ClusterError('trusted cluster and node UIDs are required')
    if not isinstance(expected_version, str) or not re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+[+A-Za-z0-9.-]*', expected_version):
        raise ClusterError('explicit Kubernetes version required')
    if not isinstance(node_name, str) or not re.fullmatch(r'[a-z0-9][a-z0-9.-]*', node_name):
        raise ClusterError('invalid expected node name')
    stage = 'namespace_read'
    try:
        ns = json.loads(run('get', 'namespace', 'kube-system', '-o', 'json'))
        stage = 'nodes_read'
        nodes = json.loads(run('get', 'nodes', '-o', 'json'))['items']
        if ns['metadata']['uid'] != cluster_uid:
            raise ClusterError('cluster identity differs from receipt', 'cluster_identity_mismatch')
        selected = [node for node in nodes if node.get('metadata', {}).get('name') == node_name]
        if len(selected) != 1:
            raise ClusterError('expected node not uniquely present', 'node_receipt_match')
        node = selected[0]
        if (node['metadata']['uid'] != node_uid
                or node['status']['nodeInfo']['kubeletVersion'] != expected_version
                or not any(x['type'] == 'Ready' and x['status'] == 'True' for x in node['status']['conditions'])):
            raise ClusterError('node identity or readiness differs from receipt', 'node_receipt_match')
        return {'cluster_uid': cluster_uid, 'node_uid': node_uid, 'node_name': node_name}
    except ClusterError as exc:
        if exc.stage == 'cluster_identity':
            raise ClusterError(stage, stage) from None
        raise
    except (KeyError, TypeError, json.JSONDecodeError, ValueError):
        raise ClusterError(stage, stage) from None
