#!/usr/bin/env python3
"""Pure comparison of fixed schema inputs; provides no deployment authorization.

The caller must authenticate the source commits, image digest, and origin of
all bytes. This module only checks content consistency; it does not read or
modify a live database and does not emit a trusted baseline receipt.
"""
import hashlib
import json
import re


def digest(data):
    if not isinstance(data, bytes) or not data or len(data) > 2_000_000:
        raise ValueError('schema input missing or oversized')
    return hashlib.sha256(data).hexdigest()


def inspect(manifest_bytes, native_sql, billing_sql, image_manifest_bytes, *, accounts_sha, billing_sha, accounts_image):
    if any(not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{40}', value)
           for value in (accounts_sha, billing_sha)):
        raise ValueError('fixed source commits required')
    if not isinstance(accounts_image, str) or not re.fullmatch(r'ghcr\.io/ai-workspace-services/accounts@sha256:[0-9a-f]{64}', accounts_image):
        raise ValueError('fixed Accounts image digest required')
    native_digest, billing_digest = digest(native_sql), digest(billing_sql)
    manifest_digest, image_digest = digest(manifest_bytes), digest(image_manifest_bytes)
    source, image = json.loads(manifest_bytes), json.loads(image_manifest_bytes)
    fields = ('format', 'migration_version', 'schema_sha256', 'business_table_count', 'business_tables')
    if not isinstance(source, dict) or not isinstance(image, dict) or any(source.get(k) != image.get(k) for k in fields):
        raise ValueError('image schema differs from source manifest')
    tables = source.get('business_tables')
    if (type(source.get('format')) is not int or source['format'] != 1 or
        type(source.get('migration_version')) is not int or source['migration_version'] <= 0 or
        source.get('schema_sha256') != native_digest or not isinstance(tables, list) or
        type(source.get('business_table_count')) is not int or source['business_table_count'] != len(tables) or
        not tables or len(tables) != len(set(tables)) or
        any(not isinstance(t, str) or not re.fullmatch(r'[a-z][a-z0-9_]*', t) for t in tables)):
        raise ValueError('invalid native schema source')
    document = {'schema': 'sit-data-input/v1', 'accounts_sha': accounts_sha, 'billing_sha': billing_sha,
                'accounts_image': accounts_image, 'accounts_manifest_sha256': manifest_digest,
                'accounts_native_sha256': native_digest, 'billing_schema_sha256': billing_digest,
                'image_manifest_sha256': image_digest, 'migration_version': source['migration_version'],
                'business_table_count': len(tables)}
    encoded = json.dumps(document, sort_keys=True, separators=(',', ':')).encode()
    return {'inputs': document, 'data_baseline_sha256': hashlib.sha256(encoded).hexdigest(),
            'evidence_status': 'INPUTS_ONLY', 'database_status': 'NOT_RUN'}
