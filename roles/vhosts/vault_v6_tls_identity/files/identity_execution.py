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


def execute(mode, raw, checksum, bootstrap_sha, client_factory, collect=cluster_identity.collect):
    """Configure or verify using only identity material collected in this process."""
    if mode not in ('configure', 'verify'):
        raise IdentityError('unsupported mode')
    receipt = receipt_contract(raw, checksum, bootstrap_sha)
    material = collect(receipt['cluster_uid'], receipt['node_uid'], receipt['version'])
    if mode == 'configure':
        admin = client_factory(True)
        vault_contract.provision(admin, material['issuer'], material['public_keys'])
        result = {'result': 'VAULT_TLS_IDENTITY_CONFIGURED'}
    else:
        result = vault_contract.verify(lambda token: client_factory(False, token), material['issuer'],
                                       material['good_jwt'], material['wrong_audience_jwt'],
                                       material['wrong_service_account_jwt'])
    # No JWT, Vault token, secret value or full discovery document enters evidence.
    result.update({'node': NODE, 'cluster_uid': receipt['cluster_uid'], 'node_uid': receipt['node_uid'],
                   'bootstrap_gitops_sha': bootstrap_sha, 'bootstrap_receipt_sha256': checksum})
    return result
