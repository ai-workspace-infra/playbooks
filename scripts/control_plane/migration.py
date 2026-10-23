#!/usr/bin/env python3
"""Invoke the pinned Accounts API client; verify recovery binding before apply."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
sys.dont_write_bytecode = True
import uuid

from entrypoint import HASH, Refused, fields, matches, protected, require


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def future(value):
    require(isinstance(value, str) and value.endswith('Z'), 'Explicit UTC expiry required')
    require(datetime.fromisoformat(value.replace('Z', '+00:00')) > datetime.now(timezone.utc), 'Evidence or authentication is expired')


def identifier(value):
    try:
        return str(uuid.UUID(value)) == value
    except (ValueError, AttributeError, TypeError):
        return False


def validate_policy(policy, action):
    fields(policy, ['schema_version', 'kind', 'environment', 'source', 'target'], ['generation', 'example_only'])
    require(policy.get('example_only', False) is False and type(policy['schema_version']) is int
            and policy['schema_version'] == 1 and policy['kind'] == 'accounts-migration'
            and policy['environment'] in ('sit', 'uat', 'prod'), 'Unsupported migration policy')
    for side, peers in (('source', 'allowed_target_urls'), ('target', 'allowed_source_urls')):
        endpoint = policy[side]
        fields(endpoint, ['url', peers, 'auth'])
        require(matches(r'https://[a-z][a-z0-9-]*(?:\.[a-z0-9][a-z0-9-]*)+(?::[0-9]{1,5})?', endpoint['url']),
                'Migration requires literal HTTPS Accounts origins')
        opposite = policy['target' if side == 'source' else 'source']['url']
        require(isinstance(endpoint[peers], list) and opposite in endpoint[peers], 'Migration peers must be reciprocally approved')
        auth = endpoint['auth']
        fields(auth, ['scopes', 'expires_at'], ['kv_ref', 'token_env'])
        require(('kv_ref' in auth) != ('token_env' in auth), 'Each endpoint needs one credential reference')
        if 'token_env' in auth:
            require(matches(r'[A-Z][A-Z0-9_]*', auth['token_env']) and auth['token_env'] != 'INTERNAL_SERVICE_TOKEN',
                    'Dedicated token environment reference required')
        else:
            require(matches('kv/' + policy['environment'] + r'/[A-Za-z0-9_/-]+', auth['kv_ref']),
                    'Migration Vault reference must be environment-scoped')
        if side == 'target' or action == 'preview':
            future(auth['expires_at'])
        scopes = auth['scopes']
        require(scopes == ['source.read'] if side == 'source' else isinstance(scopes, list)
                and 'target.plan' in scopes and set(scopes) <= {'target.plan', 'target.apply'}, 'Migration auth scope differs')
    require(policy['source']['url'] != policy['target']['url'], 'Source and target origins must differ')
    source_auth, target_auth = policy['source']['auth'], policy['target']['auth']
    require((source_auth.get('token_env'), source_auth.get('kv_ref')) !=
            (target_auth.get('token_env'), target_auth.get('kv_ref')), 'Source and target credentials must be distinct')


def auth_args(policy, side):
    auth = policy[side]['auth']
    return ['--' + side + '-token-env', auth['token_env']] if 'token_env' in auth else ['--' + side + '-auth-kv', auth['kv_ref']]


def invoke(client, args):
    # Client owns HTTP, authentication, redirects, transaction and idempotency.
    # Never echo stderr: it may contain private endpoint/driver facts.
    result = subprocess.run([str(client), 'transfer-api', *args], capture_output=True, text=True, timeout=360)
    require(result.returncode == 0, 'Accounts migration client failed or API unsupported; no fallback')
    value = json.loads(result.stdout)
    require(isinstance(value, dict), 'Invalid Accounts client receipt')
    return value


def validate_recovery(request, policy, plan):
    receipt_path = protected(request['backup_receipt'])
    require(matches(HASH, request['backup_receipt_sha256']) and digest(receipt_path) == request['backup_receipt_sha256'],
            'Reviewed backup/restore receipt checksum differs')
    evidence = json.loads(receipt_path.read_text())
    fields(evidence, ['format', 'backup_id', 'backup_sha256', 'target_identity_sha256', 'target_schema_sha256',
                     'target_baseline_sha256', 'restored_baseline_sha256', 'restore_verified', 'verified_at', 'expires_at'])
    require(evidence['format'] == 1 and evidence['restore_verified'] is True, 'Accounts backup gate is not verified')
    for key in ('target_identity_sha256', 'target_schema_sha256', 'target_baseline_sha256'):
        require(evidence.get(key) == plan.get(key) and matches(HASH, plan.get(key)), 'Backup/restore evidence is not bound to the current plan')
    require(plan.get('plan_id') == request['plan_id'] and plan.get('plan_sha256') == request['plan_sha256']
            and plan.get('conflict_rows') == 0 and plan.get('state') in ('preview', 'applied'), 'Plan differs or has conflicts')
    future(plan['expires_at'])
    future(evidence['expires_at'])
    require(evidence['restored_baseline_sha256'] == plan['target_baseline_sha256'], 'Restored API baseline differs')
    verified = datetime.fromisoformat(evidence['verified_at'].replace('Z', '+00:00'))
    now = datetime.now(timezone.utc)
    require(0 <= (now - verified).total_seconds() <= 86400 and
            datetime.fromisoformat(evidence['expires_at'].replace('Z', '+00:00')) <= verified + timedelta(hours=24),
            'Backup verification timestamp is invalid')
    proof_path = protected(request['owner_proof'])
    require(matches(HASH, request['owner_proof_sha256']) and digest(proof_path) == request['owner_proof_sha256'],
            'Authenticated owner proof checksum differs')
    proof = json.loads(proof_path.read_text())
    require(proof.get('kind') == 'playbooks-api-backup-proof' and proof.get('owner_sha') == request['owner_sha']
            and matches(r'[0-9a-f]{40}', request['owner_sha']) and proof.get('receipt') == evidence
            and proof.get('plan_id') == plan['plan_id'] and proof.get('plan_sha256') == plan['plan_sha256']
            and proof.get('target_writers_paused') is True, 'Owner-issued proof provenance differs')
    backup, restored = proof['backup'], proof['restore']
    require(backup.get('environment') == policy['environment'] and backup.get('archive_sha256') == evidence['backup_sha256']
            and backup.get('encrypted') is True and backup.get('durable') is True and backup.get('full_database') is True
            and restored.get('full_database') is True and backup.get('checkpoint_id') == evidence['backup_id'], 'Native backup binding differs')
    for key in ('environment', 'schema_sha256', 'data_sha256', 'archive_sha256', 'schema_version', 'migration_dirty',
                'database_system_identifier', 'baseline_id', 'source_database_id'):
        require(backup.get(key) == restored.get(key) and backup.get(key) is not None, 'Independent native restore differs')
    require(backup.get('status') == restored.get('status') == 'passed' and restored.get('isolated_restore_verified') is True
            and restored.get('restored_data_matches') is True and restored.get('source_restore_schema_matches') is True
            and backup.get('migration_dirty') == 'false', 'Independent restore did not pass')
    for key in ('target_identity_sha256', 'target_schema_sha256', 'target_baseline_sha256', 'tool_sha256'):
        require(proof['fresh_plan'].get(key) == plan.get(key) and bool(plan.get(key)), 'Fresh canonical API baseline differs')
    if request.get('backup_archive'):
        require(digest(protected(request['backup_archive'])) == evidence['backup_sha256'], 'Encrypted archive checksum differs')


def execute(request):
    fields(request, ['policy', 'client', 'client_sha256'], ['action', 'plan_id', 'plan_sha256', 'snapshot_id',
        'backup_receipt', 'backup_receipt_sha256', 'backup_archive', 'owner_proof', 'owner_proof_sha256', 'owner_sha', 'check'])
    action = request.get('action', 'preview')
    require(action in ('preview', 'status', 'apply'), 'Unsupported migration action')
    policy = request['policy']
    validate_policy(policy, action)
    client = Path(request['client']).resolve(strict=True)
    require(client.is_file() and os.access(client, os.X_OK) and matches(HASH, request['client_sha256'])
            and digest(client) == request['client_sha256'], 'Pinned Accounts client checksum differs')
    if action in ('apply', 'status'):
        require(identifier(request.get('plan_id')), 'Original canonical plan ID is required')
    if action == 'apply':
        require(matches(HASH, request.get('plan_sha256')), 'Apply requires the exact reviewed plan hash')
    if request.get('check'):
        return {'status': 'validated_plan', 'action': action, 'api_calls': False, 'business_acceptance': False}
    target = ['--target-url', policy['target']['url'], *auth_args(policy, 'target')]
    if action == 'apply':
        # Only GET/status is allowed until all evidence is verified against the live persisted plan.
        current = invoke(client, [*target, '--status', '--plan-id', request['plan_id']])
        require(current.get('plan_id') == request['plan_id'] and current.get('plan_sha256') == request['plan_sha256'],
                'Persisted plan differs from the original reviewed request')
        if current.get('state') == 'applied':
            # Authenticated status is subject-scoped in Accounts. This is a read-only
            # replay receipt, never a new apply; obsolete backup/plan expiry is irrelevant.
            return {'status': 'already_applied', 'action': 'status', 'plan': current,
                    'business_acceptance': False, 'cutover': False}
        require('target.apply' in policy['target']['auth']['scopes'], 'Target credential does not authorize new apply')
        require(all(request.get(k) for k in ('backup_receipt', 'backup_receipt_sha256', 'owner_proof', 'owner_proof_sha256', 'owner_sha')),
                'New apply requires encrypted backup and independent restore evidence')
        validate_recovery(request, policy, current)
        result = invoke(client, [*target, '--apply', '--plan-id', request['plan_id'], '--plan-sha256', request['plan_sha256']])
        require(result.get('plan_id') == request['plan_id'] and result.get('plan_sha256') == request['plan_sha256']
                and result.get('state') == 'applied', 'Committed API receipt differs from reviewed plan')
    elif action == 'status':
        result = invoke(client, [*target, '--status', '--plan-id', request['plan_id']])
    else:
        args = [*target, '--source-url', policy['source']['url'], *auth_args(policy, 'source')]
        for name in ('plan_id', 'snapshot_id'):
            if request.get(name):
                require(identifier(request[name]), 'Canonical recovery request UUID required')
                args += ['--' + name.replace('_', '-'), request[name]]
        result = invoke(client, args)
        require(result.get('conflict_rows') == 0, 'Migration preview has conflicts')
    return {'status': 'client_completed', 'action': action, 'plan': result, 'business_acceptance': False, 'cutover': False}


if __name__ == '__main__':
    try:
        print(json.dumps(execute(json.load(sys.stdin)), sort_keys=True))
    except Exception:
        print('Core migration refused; private output withheld; no legacy fallback.', file=sys.stderr)
        sys.exit(2)
