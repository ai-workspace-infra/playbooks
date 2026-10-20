"""Credential-free policy-role references for an isolated logical restore."""
import json


def restore_policy_role_references(query, isolated_container):
    roles = json.loads(query('account', "SELECT COALESCE(json_agg(DISTINCT r.rolname),'[]') "
                            "FROM pg_policy p CROSS JOIN LATERAL unnest(p.polroles) pr(role_oid) "
                            "JOIN pg_roles r ON r.oid=pr.role_oid WHERE r.rolname<>'postgres'"))
    for role in roles:
        query('postgres', 'CREATE ROLE "' + role.replace('"', '""') + '" NOLOGIN;', isolated_container)
    return len(roles)
