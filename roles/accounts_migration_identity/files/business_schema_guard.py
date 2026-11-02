#!/usr/bin/env python3
"""Build a fail-closed SQL guard from a verified restore's table fingerprints.

This module builds SQL only. It is not a backup receipt or an executor. The
caller must verify backup provenance, target identity and the restored database.
"""
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


def build_guard(fingerprints, system_identifier, database_oid):
    if not isinstance(system_identifier, str) or not re.fullmatch(r'[0-9]+', system_identifier):
        raise ValueError('invalid expected system identifier')
    if not isinstance(database_oid, str) or not re.fullmatch(r'[0-9]+', database_oid):
        raise ValueError('invalid expected database OID')
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
             "DO $$ BEGIN IF (SELECT array_agg(c.relname::text ORDER BY c.relname::text COLLATE \"C\") FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p')) IS DISTINCT FROM ARRAY[" + expected + "]::text[] THEN RAISE EXCEPTION 'target public table scope differs'; END IF; END $$;"]
    for name in names:
        count, digest = fingerprints[name]
        guard.append('DO $$ DECLARE n bigint; h text; BEGIN ' + fingerprint_query(name).replace('SELECT count(*)::bigint,', 'SELECT count(*)::bigint,', 1).replace(' FROM public.', ' INTO n,h FROM public.', 1) + "; IF n <> " + str(count) + " OR h <> '" + digest + "' THEN RAISE EXCEPTION 'target data differs from restore: " + name + "'; END IF; END $$;")
    return '\n'.join(guard) + '\n'
