import importlib.util
from pathlib import Path
import pytest

P = Path(__file__).resolve().parents[1] / 'roles/platform/dev_web_saas_seed/files/contracts.py'
spec = importlib.util.spec_from_file_location('seed_contracts', P)
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


def test_dev_scope_and_independent_routing():
    for stage, namespace in c.STAGES.items():
        c.target(stage, namespace)
    with pytest.raises(ValueError):
        c.target('prod', 'web-saas-uat')
    with pytest.raises(ValueError):
        c.target('sit', 'web-saas-uat')
    assert c.identity(c.STAGES['sit'], 'xc_accounts_rw') != c.identity(c.STAGES['uat'], 'xc_accounts_rw')
    assert c.identity(c.STAGES['sit'], 'xc_accounts_rw') != c.identity(c.STAGES['sit'], 'xc_billing_rw')


def test_unknown_schema_refused():
    with pytest.raises(ValueError, match='checksum'):
        c.verify_sources({}, b'', b'')
    with pytest.raises(ValueError):
        c.grants(['users; DROP DATABASE account'])


def test_billing_has_no_user_or_owner_privileges():
    sql = c.grants(['table_' + str(i) for i in range(77)])
    billing = '\n'.join(line for line in sql.splitlines() if 'GRANT' in line and 'TO xc_billing_rw' in line)
    assert 'users' not in billing
    assert 'DELETE' not in billing
    assert 'ALL TABLES' not in billing
    assert 'schema_migrations TO xc_billing_rw' not in sql
    assert 'OWNER TO' not in sql and 'SUPERUSER' not in sql



def test_owned_secret_reused_without_rotation_and_foreign_owner_refused(monkeypatch):
    import sys
    import base64
    monkeypatch.setitem(sys.modules, 'contracts', c)
    import types
    monkeypatch.setitem(sys.modules, 'platform_scope', types.SimpleNamespace(preflight=lambda *args: None))
    spec = importlib.util.spec_from_file_location('seed_owner', P.parent / 'seed.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    owner = module.Owner.__new__(module.Owner)
    owner.ns = c.STAGES['sit']
    existing = {'metadata': {'name': 'web-saas-db-admin', 'labels': {'seed-owner': module.OWNER}},
                'data': {'password': base64.b64encode(b'old-runtime-test-value').decode()}}
    owner.k = lambda *args: {'items': [existing]}
    owner.apply = lambda resource: pytest.fail('existing secret must not rotate')
    assert owner.secret('web-saas-db-admin', {'password': 'different-test-value'})['password'] == 'old-runtime-test-value'
    existing['metadata']['labels'] = {}
    with pytest.raises(module.Blocked, match='unknown owner'):
        owner.secret('web-saas-db-admin', {'password': 'different-test-value'})
