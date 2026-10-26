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


def test_complete_initialize_returns_receipt_after_all_probes(monkeypatch):
    import sys
    import types
    import json
    monkeypatch.setitem(sys.modules, 'contracts', c)
    monkeypatch.setitem(sys.modules, 'platform_scope', types.SimpleNamespace(preflight=lambda *args: None))
    spec = importlib.util.spec_from_file_location('seed_complete', P.parent / 'seed.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sha = 'a' * 40
    source = {'metadata': {'name': 'fixed-source'}, 'spec': {'url':'https://github.com/ai-workspace-infra/gitops.git', 'ref':{'commit':sha}}, 'status':{'artifact':{'revision':'sha1:'+sha}}}
    tables = ['table_' + str(i) for i in range(77)]
    manifest = {'format':1, 'migration_version':2026102501, 'schema_sha256':c.SCHEMA_SHA, 'business_table_count':77, 'business_tables':tables}
    class Response:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def read(self,*args): return json.dumps(manifest).encode()
    monkeypatch.setattr(module.urllib.request, 'urlopen', lambda *args,**kwargs: Response())
    # Content checksums are exercised by the real PG integration sample. This test
    # covers complete executor sequencing and receipt construction without K8s writes.
    monkeypatch.setattr(module, 'verify_sources', lambda *args: tables)
    owner = module.Owner.__new__(module.Owner)
    owner.ns = c.STAGES['sit']
    owner.r = {'candidate_sha':sha,'stage':'sit','instance_id':'test-instance','node_uid':'node','cluster_uid':'cluster','namespace_uid':'namespace-original'}
    def k(*args):
        if 'namespace' in args: return {'metadata':{'uid':'namespace-original','labels':{'environment':'sit'}}}
        if 'gitrepositories.source.toolkit.fluxcd.io' in args: return {'items':[source]}
        if 'gitrepository' in args: return source
        if 'secrets' in args: return {'items':[]}
        if 'pod' in args: return {'spec':{'containers':[{'image':module.PG_IMAGE}]},'status':{'containerStatuses':[{'imageID':'ghcr.io/ai-workspace-services/postgresql@'+module.PG_AMD64_DIGEST}]}}
        raise AssertionError(args)
    owner.k = k
    owner.raw = lambda *args,**kwargs: ''
    writes = []
    owner.secret = lambda name,fields: writes.append(name) or fields
    def psql(query, **kwargs):
        if 'CREATE ROLE' in query:
            assert kwargs.get('sensitive') is True
        if 'SHOW log_statement' in query: return 'none\n-1\n-1\n'
        if 'SELECT count(*)' in query: return '0\n'
        if 'SELECT tablename' in query: return '\n'.join(tables+['schema_migrations','cloud_vendor_costs'])
        return ''
    owner.psql = psql
    jobs = []
    def job(purpose,*args):
        jobs.append(purpose)
        if purpose == 'manifest': return json.dumps(manifest)
        if purpose.startswith('probe-'): return 'identity_probes_verified\n'
        return '{}'
    owner.job = job
    receipt = owner.initialize()
    assert receipt['schema_version'] == 'dev-lane-seed/v1'
    assert receipt['candidate_sha'] == sha and receipt['result'] == 'verified'
    assert jobs == ['manifest','native-init','probe-xc-accounts-rw','probe-xc-billing-rw','probe-xc-lane-audit']
    assert writes[-4:] == ['web-saas-accounts-runtime','web-saas-billing-runtime','web-saas-console-runtime','web-saas-content-runtime']
    assert receipt['namespace_uid'] == 'namespace-original'
    # Simulate deleting and recreating the same named namespace during init.
    original_k = owner.k
    namespace_reads = []
    def recreated_during_init(*args):
        result = original_k(*args)
        if 'namespace' in args:
            namespace_reads.append(True)
            if len(namespace_reads) > 1:
                result['metadata']['uid'] = 'namespace-recreated'
        return result
    owner.k = recreated_during_init
    with pytest.raises(module.Blocked, match='namespace UID'):
        owner.initialize()
    owner.k = original_k
    # Unknown OCI runtime digests fail before any Secret or SQL write.
    original_k = owner.k
    def bad_image(*args):
        result = original_k(*args)
        if 'pod' in args: result['status']['containerStatuses'][0]['imageID'] = 'sha256:'+'b'*64
        return result
    owner.k = bad_image
    owner.psql = lambda *args: pytest.fail('unverified image must fail before SQL')
    with pytest.raises(module.Blocked, match='imageID'):
        owner.initialize()


def test_seed_adapter_rejects_unbound_artifact_before_transport(monkeypatch, tmp_path):
    import sys
    import hashlib
    monkeypatch.syspath_prepend(str(P.parents[4] / 'scripts'))
    # Resolve from repository root explicitly; no operational entry is executed.
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root / 'scripts'))
    import run_dev_web_saas_seed as adapter
    artifact = tmp_path / 'receipt.json'
    artifact.write_text('{"untrusted":true}')
    with pytest.raises(ValueError, match='checksum mismatch'):
        adapter.read_bound(artifact, 'a'*64)
    assert adapter.read_bound(artifact, hashlib.sha256(artifact.read_bytes()).hexdigest()) == {'untrusted':True}
    # Reading valid bytes alone does not authenticate them or grant execution.


def test_seed_admin_refuses_stale_and_recreated_namespace(monkeypatch):
    import sys
    import types
    monkeypatch.setitem(sys.modules, 'contracts', c)
    monkeypatch.setitem(sys.modules, 'platform_scope', types.SimpleNamespace(preflight=lambda *args: None))
    spec = importlib.util.spec_from_file_location('seed_namespace', P.parent / 'seed.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    owner = module.Owner.__new__(module.Owner)
    owner.ns = c.STAGES['sit']
    owner.r = {'stage':'sit','namespace_uid':'original-uid'}
    live = {'metadata':{'uid':'original-uid','labels':{'environment':'sit'}}}
    owner.k = lambda *args: live
    writes = []
    def create_admin(name, fields):
        writes.append(name)
        # Same name, different live identity after a simulated delete/recreate.
        live['metadata']['uid'] = 'recreated-uid'
    owner.secret = create_admin
    with pytest.raises(module.Blocked, match='namespace UID'):
        owner.seed_admin()
    assert writes == ['web-saas-db-admin']
    writes.clear()
    with pytest.raises(module.Blocked, match='namespace UID'):
        owner.seed_admin()
    assert writes == []  # Stale input is rejected before any second Secret write.
