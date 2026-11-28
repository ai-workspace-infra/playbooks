"""Build reviewable per-object PostgreSQL grants for independent runtimes.

This module does not connect to a database, consume secrets or apply SQL. The
caller must compare these declarations with a verified live catalog and execute
only through a separately implemented owner. Never use this plan as evidence of
applied privileges or RLS acceptance.
"""
from dataclasses import dataclass
import hashlib
import json
import re

IDENTIFIER = re.compile(r'[a-z_][a-z0-9_]{0,62}\Z')
PRIVILEGES = frozenset(('SELECT', 'INSERT', 'UPDATE', 'DELETE'))


@dataclass(frozen=True)
class RuntimeGrantPlan:
    statements: tuple[str, ...]
    sha256: str


def identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value) or value.startswith('pg_') or value == 'public':
        raise ValueError('invalid or privileged identifier')
    return '"' + value + '"'


def plan_runtime_grants(spec):
    """Compile explicit runtime identities, per-table rights and role-only policies.

    spec is a mapping with database, schema, runtimes and rls_policies. Each
    runtime contains role and tables; each table maps to a nonempty list of
    SELECT/INSERT/UPDATE/DELETE. Every policy names a runtime role and one table
    on which that role has all four rights. The database and schema are explicit
    caller-supplied deployment facts. No wildcard or PUBLIC grants are produced.
    """
    if not isinstance(spec, dict) or set(spec) != {'database', 'schema', 'runtimes', 'rls_policies'}:
        raise ValueError('invalid runtime grant specification')
    database = identifier(spec['database'])
    schema_name = spec['schema']
    # public is the standard application schema, never an actor to grant.
    schema = '"public"' if schema_name == 'public' else identifier(schema_name)
    runtimes = spec['runtimes']
    policies = spec['rls_policies']
    if not isinstance(runtimes, list) or not runtimes or not isinstance(policies, list):
        raise ValueError('explicit runtime and policy lists required')
    statements = []
    roles = {}
    for runtime in runtimes:
        if not isinstance(runtime, dict) or set(runtime) != {'role', 'tables'}:
            raise ValueError('invalid runtime declaration')
        role_name, tables = runtime['role'], runtime['tables']
        role = identifier(role_name)
        if role_name in roles or not isinstance(tables, dict) or not tables:
            raise ValueError('duplicate runtime or empty table scope')
        roles[role_name] = tables
        statements.extend((f'GRANT CONNECT ON DATABASE {database} TO {role};',
                           f'GRANT USAGE ON SCHEMA {schema} TO {role};'))
        for table_name, privileges in sorted(tables.items()):
            table = identifier(table_name)
            if (not isinstance(privileges, list) or not privileges or
                    any(not isinstance(p, str) for p in privileges) or
                    len(set(privileges)) != len(privileges) or not set(privileges) <= PRIVILEGES):
                raise ValueError('invalid table privileges')
            rights = ', '.join(p for p in ('SELECT', 'INSERT', 'UPDATE', 'DELETE') if p in privileges)
            statements.append(f'GRANT {rights} ON TABLE {schema}.{table} TO {role};')
    seen = set()
    for policy in policies:
        if not isinstance(policy, dict) or set(policy) != {'role', 'table', 'name'}:
            raise ValueError('invalid policy declaration')
        role_name, table_name = policy['role'], policy['table']
        policy_name = identifier(policy['name'])
        role, table = identifier(role_name), identifier(table_name)
        key = (table_name, policy['name'])
        if (key in seen or role_name not in roles or table_name not in roles[role_name]
                or set(roles[role_name][table_name]) != PRIVILEGES):
            raise ValueError('policy must be unique and scoped to an explicitly granted runtime')
        seen.add(key)
        statements.append(f'CREATE POLICY {policy_name} ON {schema}.{table} FOR ALL TO {role} USING (true) WITH CHECK (true);')
    digest = hashlib.sha256(json.dumps(statements, separators=(',', ':')).encode()).hexdigest()
    return RuntimeGrantPlan(tuple(statements), digest)
