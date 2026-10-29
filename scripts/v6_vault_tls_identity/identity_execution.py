"""Compose receipt binding, private cluster collection and Vault identity checks.

The caller must independently authenticate the Actions run and receipt checksum,
provide an authenticated private kubeconfig, and acquire a scoped administrator
identity for create. This module does not by itself establish those trust anchors.
"""
import hashlib
import json
import re

import cluster_identity
import vault_contract

NODE = 'prod-k3s-v6'
VERSION = 'v1.37.1+k3s1'
IDENTITY = vault_contract.IdentitySpec(
    mount='jwt-prod-k3s-v6', role='prod-k3s-v6-vault-tls-reader', policy='prod-k3s-v6-vault-tls-reader',
    namespace='vault-prod', service_account='vault-tls-reader', audience='vault-prod-k3s-v6-eso',
    denied_audience='vault-prod-k3s-v6-denied-probe', probe_service_account='default',
    allowed_path='kv/data/CICD/domains/svc.plus', denied_path='kv/data/CICD/domains/v6-authorization-denied-probe')


class IdentityError(Exception):
    pass


def receipt_contract(raw, checksum, bootstrap_sha):
    if not re.fullmatch(r'[0-9a-f]{64}', checksum or '') or hashlib.sha256(raw).hexdigest() != checksum:
        raise IdentityError('bootstrap receipt checksum mismatch')
    if not re.fullmatch(r'[0-9a-f]{40}', bootstrap_sha or ''):
        raise IdentityError('expected bootstrap SHA is required')
    try:
        data = json.loads(raw)
        if (data['result'] != 'PAUSED_BOOTSTRAP_VERIFIED' or data['node'] != NODE
                or data['version'] != VERSION or data['gitops_sha'] != bootstrap_sha
                or data['audit']['result'] != 'AUDIT_ALLOW_DENY_VERIFIED'
                or data['common']['result'] != 'COMMON_NODE_VERIFIED'
                or data['common']['node'] != NODE):
            raise IdentityError('bootstrap receipt does not establish the required boundary')
        for key in ('cluster_uid', 'node_uid'):
            if not re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', data[key]):
                raise IdentityError('bootstrap receipt identity is invalid')
    except (KeyError, TypeError, ValueError):
        raise IdentityError('invalid bootstrap receipt') from None
    return data
