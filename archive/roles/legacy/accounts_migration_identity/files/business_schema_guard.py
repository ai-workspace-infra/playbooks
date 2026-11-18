#!/usr/bin/env python3
"""Build a fail-closed SQL guard from a verified restore's table fingerprints.

This module builds SQL only. It is not a backup receipt or an executor. The
caller must verify backup provenance, target identity and the restored database.
"""
import hashlib
import re

IDENTIFIER = re.compile(r'^[a-z_][a-z0-9_]{0,62}$')
SHA256 = re.compile(r'^[a-f0-9]{64}$')


def _identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError('invalid public table identifier')
    return '"' + value + '"'


def fingerprint_query(table):
    table = _identifier(table)
    # JSON text and SHA-256 digests stay inside PostgreSQL; rows never leave it.
    return ("SELECT count(*)::bigint, encode(sha256(convert_to("
            "coalesce(string_agg(encode(sha256(convert_to(row_to_json(t)::text,'UTF8')),'hex'),"
            "'' ORDER BY encode(sha256(convert_to(row_to_json(t)::text,'UTF8')),'hex')),''),"
            "'UTF8')),'hex') FROM public." + table + " t")


def catalog_fingerprint_query():
    # This is target drift detection, not a proof that the target catalog is
    # equivalent to a backup or safe for a given migration.
    return """SELECT encode(sha256(convert_to(coalesce(string_agg(v,E'\\n' ORDER BY v COLLATE "C"),''),'UTF8')),'hex') FROM (
SELECT jsonb_build_array('table',c.relname,c.relkind,c.relpersistence,c.relrowsecurity,c.relforcerowsecurity,c.relowner,c.relacl::text)::text AS v FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p')
UNION ALL SELECT jsonb_build_array('namespace',n.nspname,n.nspowner,n.nspacl::text)::text FROM pg_namespace n WHERE n.nspname='public'
UNION ALL SELECT jsonb_build_array('sequence',c.relname,c.relowner,c.relacl::text,s.seqstart,s.seqincrement,s.seqmax,s.seqmin,s.seqcache,s.seqcycle)::text FROM pg_sequence s JOIN pg_class c ON c.oid=s.seqrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'
UNION ALL SELECT jsonb_build_array('column',c.relname,a.attnum,a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,a.attidentity,a.attgenerated,pg_get_expr(d.adbin,d.adrelid))::text FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum WHERE n.nspname='public' AND c.relkind IN ('r','p') AND a.attnum>0 AND NOT a.attisdropped
UNION ALL SELECT jsonb_build_array('constraint',c.relname,k.conname,pg_get_constraintdef(k.oid,true),k.convalidated)::text FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p')
UNION ALL SELECT jsonb_build_array('index',c.relname,i.relname,pg_get_indexdef(i.oid))::text FROM pg_index x JOIN pg_class c ON c.oid=x.indrelid JOIN pg_class i ON i.oid=x.indexrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'
UNION ALL SELECT jsonb_build_array('trigger',c.relname,t.tgname,t.tgenabled,pg_get_triggerdef(t.oid,true))::text FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND NOT t.tgisinternal
UNION ALL SELECT jsonb_build_array('policy',c.relname,p.polname,p.polcmd,p.polpermissive,p.polroles::text,pg_get_expr(p.polqual,p.polrelid),pg_get_expr(p.polwithcheck,p.polrelid))::text FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'
UNION ALL SELECT jsonb_build_array('function',p.proname,pg_get_function_identity_arguments(p.oid),p.prosrc,p.proowner,p.proacl::text,p.prorettype,p.prolang,p.provolatile,p.prosecdef,p.proconfig::text)::text FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public'
UNION ALL SELECT jsonb_build_array('extension',extname,extversion)::text FROM pg_extension
) AS catalog(v)"""


def build_guard(fingerprints, system_identifier, database_oid, catalog_sha256):
    if not isinstance(system_identifier, str) or not re.fullmatch(r'[0-9]+', system_identifier):
        raise ValueError('invalid expected system identifier')
    if not isinstance(database_oid, str) or not re.fullmatch(r'[0-9]+', database_oid):
        raise ValueError('invalid expected database OID')
    if not isinstance(catalog_sha256, str) or not SHA256.fullmatch(catalog_sha256):
        raise ValueError('target catalog fingerprint required')
    if not isinstance(fingerprints, dict) or not fingerprints:
        raise ValueError('restored table fingerprints required')
    names = sorted(fingerprints)
    for name in names:
        _identifier(name)
        value = fingerprints[name]
        if not isinstance(value, tuple) or len(value) != 2 or type(value[0]) is not int or value[0] < 0 or not isinstance(value[1], str) or not SHA256.fullmatch(value[1]):
            raise ValueError('invalid restored table fingerprint')
    expected = ','.join("'" + name + "'" for name in names)
    locks = ', '.join('public.' + _identifier(name) for name in names)
    guard = ["SET LOCAL lock_timeout TO '10s';", "SET LOCAL statement_timeout TO '5min';",
             "SET LOCAL TIME ZONE 'UTC';", "SET LOCAL DateStyle TO 'ISO, YMD';",
             'SET LOCAL extra_float_digits TO 3;',
             "DO $$ BEGIN IF (SELECT system_identifier::text FROM pg_control_system()) <> '" + system_identifier + "' OR (SELECT oid::text FROM pg_database WHERE datname=current_database()) <> '" + database_oid + "' THEN RAISE EXCEPTION 'target physical identity differs'; END IF; END $$;",
             'LOCK TABLE ' + locks + ' IN SHARE ROW EXCLUSIVE MODE;',
             "DO $$ BEGIN IF (SELECT array_agg(c.relname::text ORDER BY c.relname::text COLLATE \"C\") FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p')) IS DISTINCT FROM ARRAY[" + expected + "]::text[] THEN RAISE EXCEPTION 'target public table scope differs'; END IF; END $$;",
             'DO $$ DECLARE h text; BEGIN ' + catalog_fingerprint_query().replace(' FROM (', ' INTO h FROM (', 1) + "; IF h <> '" + catalog_sha256 + "' THEN RAISE EXCEPTION 'target catalog differs'; END IF; END $$;"]
    for name in names:
        count, digest = fingerprints[name]
        guard.append('DO $$ DECLARE n bigint; h text; BEGIN ' + fingerprint_query(name).replace(' FROM public.', ' INTO n,h FROM public.', 1) + "; IF n <> " + str(count) + " OR h <> '" + digest + "' THEN RAISE EXCEPTION 'target data differs from restore: " + name + "'; END IF; END $$;")
    return '\n'.join(guard) + '\n'


def compose_transaction(sql_bytes, expected_sha256, fingerprints, system_identifier, database_oid, catalog_sha256, *, dry_run):
    """Compose a pinned SQL artifact with guard; does not validate provenance or execute it."""
    if not isinstance(sql_bytes, bytes) or not isinstance(expected_sha256, str) or not SHA256.fullmatch(expected_sha256) or hashlib.sha256(sql_bytes).hexdigest() != expected_sha256:
        raise ValueError('reviewed SQL checksum differs')
    if type(dry_run) is not bool:
        raise ValueError('explicit dry-run mode required')
    try:
        sql = sql_bytes.decode('utf-8')
    except UnicodeDecodeError:
        raise ValueError('SQL must be UTF-8') from None
    begins = list(re.finditer(r'(?m)^BEGIN;\s*$', sql))
    ends = list(re.finditer(r'(?m)^COMMIT;\s*$', sql))
    if len(begins) != 1 or len(ends) != 1 or begins[0].start() >= ends[0].start() or sql[ends[0].end():].strip():
        raise ValueError('reviewed SQL transaction boundaries differ')
    checked = build_guard(fingerprints, system_identifier, database_oid, catalog_sha256)
    session = ("SELECT set_config('accounts_migration_guard.system_identifier','" + system_identifier + "',true);\n"
               "SELECT set_config('accounts_migration_guard.database_oid','" + database_oid + "',true);\n")
    end = 'ROLLBACK;' if dry_run else 'COMMIT;'
    return sql[:begins[0].end()] + '\n' + session + checked + sql[begins[0].end():ends[0].start()] + end + '\n'
