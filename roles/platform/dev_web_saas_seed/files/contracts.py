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
STAGES = {'sit': 'feature-xconnect-referral-sit', 'uat': 'web-saas-uat'}


def target(stage, namespace):
    if STAGES.get(stage) != namespace:
        raise ValueError('unsupported Dev stage/namespace')


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


def identity(namespace, role):
    if namespace not in STAGES.values() or role not in ('xc_accounts_rw', 'xc_billing_rw'):
        raise ValueError('unsupported runtime identity')
    value = {'Host': f'web-saas-postgresql.{namespace}.svc.cluster.local',
             'Port': 5432, 'Database': 'account', 'Role': role}
    return hashlib.sha256(json.dumps(value, separators=(',', ':')).encode()).hexdigest()


def grants(tables):
    """Run as the DDL owner after native init + exact Billing bootstrap SQL.

    No ownership transfer, role creation/passwords, defaults for future unknown objects,
    or schema_migrations DML. Role creation is a separate management operation.
    """
    if len(tables) != 77 or any(not re.fullmatch(r'[a-z][a-z0-9_]*', t) for t in tables):
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
