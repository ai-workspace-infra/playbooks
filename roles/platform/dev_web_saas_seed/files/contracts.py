"""Dev-only native schema contract. No execution or credential retrieval."""
import hashlib
import json
import re

ACCOUNTS_SHA = '8d1b09a6f2022af221b2a817d516952af41ced0e'
SCHEMA_SHA = 'e223518d156308c1cb2ea6919cfdccf1c0cacc03b55f9b41824ef29e4f632b13'
BILLING_SHA = '77557d796a66dcd3373d7731125890bd91ebc5d3'
BILLING_SQL_SHA = '7faf46295b419102e4bbcb8cef67db14ba3beef1d46ee945fd4acfb86541c39b'
BILLING_WRITES = ('traffic_stat_checkpoints', 'traffic_minute_buckets', 'billing_ledger',
                  'account_quota_states', 'billing_source_sync_state', 'cloud_vendor_costs')
STAGES = {'sit': 'feature-xconnect-referral-sit'}


def target(stage, namespace, tagged_lane=None):
    if tagged_lane is None:
        if STAGES.get(stage) != namespace:
            raise ValueError('unsupported Dev stage/namespace')
        return
    if not isinstance(tagged_lane, dict) or set(tagged_lane) != {'feature_id', 'feature_tag', 'candidate_sha256'}:
        raise ValueError('invalid tagged lane binding')
    feature, tag, digest = (tagged_lane.get(k) for k in ('feature_id', 'feature_tag', 'candidate_sha256'))
    if (stage != 'sit' or not isinstance(feature, str) or not re.fullmatch(r'[a-z][a-z0-9-]{1,45}', feature) or
        feature.endswith('-') or not isinstance(tag, str) or not tag.endswith('-' + feature) or
        not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', tag[:-(len(feature)+1)]) or
        not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest) or
        namespace != 'feature-' + feature + '-sit' or len(namespace) > 63):
        raise ValueError('unsupported tagged Dev stage/namespace')


def verify_sources(manifest, native_sql, billing_sql):
    if (manifest.get('schema_sha256') != SCHEMA_SHA or
            hashlib.sha256(native_sql).hexdigest() != SCHEMA_SHA or
            hashlib.sha256(billing_sql).hexdigest() != BILLING_SQL_SHA):
        raise ValueError('fixed source checksum mismatch')
    tables = manifest.get('business_tables')
    if (manifest.get('migration_version') != 2026102501 or
            not isinstance(tables, list) or len(tables) != 77 or len(set(tables)) != 77 or
            any(not re.fullmatch(r'[a-z][a-z0-9_]*', name) for name in tables)):
        raise ValueError('unexpected native schema manifest')
    return tables


def verify_tagged_sources(manifest_bytes, native_sql, billing_sql, image_manifest_bytes, inputs):
    """Verify exact candidate-bound source and compiled schema before any SQL.

    The caller must authenticate the candidate receipt and its baseline inputs.
    """
    if not isinstance(inputs, dict) or inputs.get('schema') != 'sit-data-input/v1':
        raise ValueError('verified baseline inputs required')
    values = ((manifest_bytes, 'accounts_manifest_sha256'), (native_sql, 'accounts_native_sha256'),
              (billing_sql, 'billing_schema_sha256'), (image_manifest_bytes, 'image_manifest_sha256'))
    if any(not isinstance(raw, bytes) or not raw or len(raw) > 2_000_000 or
           hashlib.sha256(raw).hexdigest() != inputs.get(key) for raw, key in values):
        raise ValueError('candidate source or image manifest differs from baseline')
    manifest, image = json.loads(manifest_bytes), json.loads(image_manifest_bytes)
    fields = ('format', 'migration_version', 'schema_sha256', 'business_table_count', 'business_tables')
    if not isinstance(manifest, dict) or not isinstance(image, dict) or any(manifest.get(k) != image.get(k) for k in fields):
        raise ValueError('compiled and source schema differ')
    tables = manifest.get('business_tables')
    count, version = inputs.get('business_table_count'), inputs.get('migration_version')
    if (type(count) is not int or not 1 <= count <= 10000 or type(version) is not int or version <= 0 or
        type(manifest.get('format')) is not int or manifest['format'] != 1 or
        manifest.get('migration_version') != version or manifest.get('schema_sha256') != inputs.get('accounts_native_sha256') or
        manifest.get('business_table_count') != count or not isinstance(tables, list) or len(tables) != count or
        any(not isinstance(t, str) or not re.fullmatch(r'[a-z][a-z0-9_]*', t) for t in tables) or len(set(tables)) != count):
        raise ValueError('invalid candidate native schema')
    return tables


def identity(namespace, role, tagged_lane=None):
    target('sit', namespace, tagged_lane)
    if role not in ('xc_accounts_rw', 'xc_billing_rw'):
        raise ValueError('unsupported runtime identity')
    value = {'Host': f'web-saas-postgresql.{namespace}.svc.cluster.local',
             'Port': 5432, 'Database': 'account', 'Role': role}
    return hashlib.sha256(json.dumps(value, separators=(',', ':')).encode()).hexdigest()


def grants(tables, expected_count=77):
    """Run as the DDL owner after native init + exact Billing bootstrap SQL.

    No ownership transfer, role creation/passwords, defaults for future unknown objects,
    or schema_migrations DML. Role creation is a separate management operation.
    """
    if (type(expected_count) is not int or not 1 <= expected_count <= 10000 or
            len(tables) != expected_count or len(set(tables)) != len(tables) or
            any(not isinstance(t, str) or not re.fullmatch(r'[a-z][a-z0-9_]*', t) for t in tables)):
        raise ValueError('unverified table list')
    account_tables = ', '.join('public.' + t for t in tables)
    billing_tables = ', '.join('public.' + t for t in BILLING_WRITES)
    return f'''REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON DATABASE account FROM PUBLIC;
GRANT CONNECT ON DATABASE account TO xc_accounts_rw, xc_billing_rw;
GRANT USAGE ON SCHEMA public TO xc_accounts_rw, xc_billing_rw;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE, DELETE ON {account_tables} TO xc_accounts_rw;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO xc_accounts_rw;
GRANT SELECT ON public.schema_migrations TO xc_accounts_rw;
GRANT SELECT, INSERT, UPDATE ON {billing_tables} TO xc_billing_rw;
GRANT SELECT ON public.account_billing_profiles TO xc_billing_rw;
'''


def audit_grants():
    """Only sanitized migration metadata, no business rows or write functions."""
    return '''REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE xc_schema_owner IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE xc_schema_owner IN SCHEMA public REVOKE ALL ON TABLES FROM PUBLIC;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO xc_accounts_rw;
CREATE VIEW public.dev_lane_schema_status AS SELECT version, dirty FROM public.schema_migrations;
GRANT CONNECT ON DATABASE account TO xc_lane_audit;
GRANT USAGE ON SCHEMA public TO xc_lane_audit;
GRANT SELECT ON public.dev_lane_schema_status TO xc_lane_audit;
'''
