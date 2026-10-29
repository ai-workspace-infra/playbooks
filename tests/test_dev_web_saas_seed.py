import importlib.util
from pathlib import Path
import pytest

SCOPE_PATH = Path(__file__).resolve().parents[1] / 'roles/platform/dev_feature_flux/files/reconcile.py'
scope_spec = importlib.util.spec_from_file_location('failure_code_scope', SCOPE_PATH)
scope = importlib.util.module_from_spec(scope_spec)
scope_spec.loader.exec_module(scope)

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
    with pytest.raises(ValueError):
        c.target('uat', 'web-saas-uat')
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
    monkeypatch.setitem(sys.modules, 'platform_scope', types.SimpleNamespace(preflight=lambda *args: None, failure_code=scope.failure_code))
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


def test_tagged_owner_rejects_baseline_and_oci_changes_before_database_operations(monkeypatch):
    import hashlib
    import json
    import sys
    import types
    monkeypatch.setitem(sys.modules, 'contracts', c)
    monkeypatch.setitem(sys.modules, 'platform_scope', types.SimpleNamespace(preflight=lambda *args: None, failure_code=scope.failure_code))
    spec = importlib.util.spec_from_file_location('tagged_seed_owner', P.parent / 'seed.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    owner = module.Owner.__new__(module.Owner)
    owner.ns = 'feature-sit-test-1483-sit'
    lane = {'feature_id': 'sit-test-1483', 'feature_tag': 'smoke-sit-test-1483', 'candidate_sha256': 'a'*64}
    inputs = {'schema': 'sit-data-input/v1'}
    baseline_sha = hashlib.sha256(json.dumps(inputs, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    pg = {'image': 'ghcr.io/ai-workspace-services/postgresql@sha256:'+'b'*64, 'platform_manifest_digest': 'sha256:'+'c'*64,
          'manifest_digest': 'sha256:'+'b'*64, 'platform': 'linux/amd64'}
    owner.r = {'stage': 'sit', 'tagged_lane': lane, 'data_baseline_inputs': inputs,
               'data_baseline_sha256': baseline_sha, 'runtime_mapping': {'images': {'postgresql': pg}}}
    assert owner.tagged_inputs()[1] == pg['image']
    inputs['schema'] = 'tampered'
    with pytest.raises(module.Blocked, match='baseline digest'): owner.tagged_inputs()
    inputs['schema'] = 'sit-data-input/v1'
    pg['platform_manifest_digest'] = 'sha256:invalid'
    with pytest.raises(module.Blocked, match='PostgreSQL OCI'): owner.tagged_inputs()
    owner.check_namespace = lambda: None
    owner.secret = lambda *args: pytest.fail('invalid OCI identity must fail before secret write')
    with pytest.raises(module.Blocked, match='PostgreSQL OCI'): owner.seed_admin()


def test_complete_initialize_returns_receipt_after_all_probes(monkeypatch):
    import sys
    import types
    import json
    monkeypatch.setitem(sys.modules, 'contracts', c)
    monkeypatch.setitem(sys.modules, 'platform_scope', types.SimpleNamespace(preflight=lambda *args: None, failure_code=scope.failure_code))
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
    def job(purpose,*args,**kwargs):
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
    # Containerd runtime imageID directly matching PG_IMAGE is accepted.
    original_k_containerd = owner.k
    def containerd_image(*args):
        result = original_k_containerd(*args)
        if 'pod' in args:
            result['status']['containerStatuses'][0]['imageID'] = module.PG_IMAGE
        return result
    owner.k = containerd_image
    writes.clear()
    jobs.clear()
    receipt2 = owner.initialize()
    assert receipt2['result'] == 'verified'
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


def test_tagged_seed_adapter_passes_verified_mapping_to_owner(monkeypatch, tmp_path):
    import contextlib
    import json
    import sys
    from types import SimpleNamespace
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'scripts'))
    import run_dev_web_saas_seed as adapter
    lane = {'feature_id': 'sit-test-1483', 'feature_tag': 'smoke-sit-test-1483', 'candidate_sha256': 'a'*64}
    mapping = {'images': {'postgresql': {'platform_manifest_digest': 'sha256:'+'b'*64}}}
    inputs = {'schema': 'sit-data-input/v1'}
    control = {'schema_version': 'dev-feature-candidate/v2', 'projection': {'tagged_lane': lane},
               'data_baseline_inputs': inputs, 'data_baseline_sha256': 'c'*64}
    observation = {'namespace': 'feature-sit-test-1483-sit', 'manifest_sha256': 'd'*64}
    args = SimpleNamespace(phase='initialize', cmdb=tmp_path/'cmdb', cmdb_sha256='e'*64, instance_id='1',
                           candidate_receipt=tmp_path/'candidate', candidate_sha256='f'*64, gitops_sha='1'*40,
                           stage='sit', namespace_uid='2'*36, prepare_receipt=tmp_path/'prepare', prepare_sha256='3'*64,
                           gitops_root=tmp_path, platform_sha='4'*40, node_uid='5'*36, cluster_uid='6'*36,
                           receipt=tmp_path/'output'/'receipt.json')
    monkeypatch.setattr(adapter, 'read_bound', lambda path, digest: {'owner_sha':'7'*40} if path == args.prepare_receipt else {})
    monkeypatch.setattr(adapter, 'validate_cmdb', lambda *a: {})
    monkeypatch.setattr(adapter, 'validate_candidate_receipt', lambda *a: control)
    monkeypatch.setattr(adapter, 'validate_receipt_inputs', lambda *a: None)
    monkeypatch.setattr(adapter, 'require_same_run_prepare', lambda *a: None)
    monkeypatch.setattr(adapter.candidate, 'validate_tree', lambda *a: observation)
    monkeypatch.setattr(adapter, 'validate_prepare_receipt', lambda *a: mapping)
    monkeypatch.setattr(adapter.candidate.SCOPE, 'validate', lambda *a: None)
    @contextlib.contextmanager
    def fake_binding(cmdb, instance_id, *, tagged, seed):
        assert tagged and seed
        yield SimpleNamespace(directory=tmp_path, env={})
    monkeypatch.setattr(adapter, 'binding', fake_binding)
    def fake_run(command, env=None):
        if command[0] == 'git': return '7'*40
        config = json.loads((tmp_path/'seed-vars.json').read_text())['dev_web_saas_seed_request']
        assert config['tagged_lane'] == lane and config['runtime_mapping'] == mapping
        assert config['data_baseline_inputs'] == inputs
        if '--syntax-check' not in command:
            receipt = {'schema_version':'dev-lane-seed/v1', 'result':'verified', 'namespace':config['namespace'],
                       **config['tagged_lane'], 'data_baseline_sha256':config['data_baseline_sha256'],
                       'schema_initialized':True, 'audit_metadata_read_verified':True, 'audit_write_and_sensitive_read_denied':True,
                       **{key:config[key] for key in ('namespace_uid','candidate_sha','instance_id','node_uid','cluster_uid')}}
            (tmp_path/'seed-receipt.json').write_text(json.dumps(receipt))
        return ''
    monkeypatch.setattr(adapter, 'run', fake_run)
    adapter.execute(args)
    assert json.loads(args.receipt.read_text())['business_acceptance'] == 'NOT_RUN'


def test_seed_admin_refuses_stale_and_recreated_namespace(monkeypatch):
    import sys
    import types
    monkeypatch.setitem(sys.modules, 'contracts', c)
    monkeypatch.setitem(sys.modules, 'platform_scope', types.SimpleNamespace(preflight=lambda *args: None, failure_code=scope.failure_code))
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


def test_tagged_seed_preflight_uses_independent_web_saas_ingress(monkeypatch):
    import sys, types
    monkeypatch.setitem(sys.modules, 'contracts', c)
    calls = []
    monkeypatch.setitem(sys.modules, 'platform_scope', types.SimpleNamespace(preflight=lambda *args: calls.append(args), failure_code=scope.failure_code))
    spec = importlib.util.spec_from_file_location('independent_tagged_seed', P.parent / 'seed.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'target', lambda *args: None)
    monkeypatch.setattr(module.Owner, 'check_namespace', lambda self: None)
    seen = []
    def k(self, *args):
        seen.append(args)
        if args == ('get', 'nodes'):
            return {'items': [{'metadata': {'uid': 'node', 'labels': {'kubernetes.io/arch': 'amd64'}}}]}
        if args == ('get', 'namespace', 'kube-system'):
            return {'metadata': {'uid': 'cluster'}}
        return {'name': args[-1]}
    monkeypatch.setattr(module.Owner, 'k', k)
    module.Owner({'stage': 'sit', 'namespace': 'feature-sit-test-1483-sit',
                  'tagged_lane': {'feature_id': 'sit-test-1483'}, 'candidate_sha': 'a' * 40,
                  'instance_id': '123', 'node_uid': 'node', 'cluster_uid': 'cluster', 'namespace_uid': 'namespace'})
    assert ('-n', 'flux-system', 'get', 'gitrepository', 'platform-config-dev') in seen
    assert ('-n', 'flux-system', 'get', 'kustomization', 'platform-ingress-dev') in seen
    assert ('-n', 'flux-system', 'get', 'kustomization', 'platform-root') not in seen
    assert len(calls) == 1


def test_partial_seed_recovery_requires_terminal_jobs_bound_roles_and_owner(monkeypatch):
    import sys, types
    monkeypatch.setitem(sys.modules, 'contracts', c)
    monkeypatch.setitem(sys.modules, 'platform_scope', types.SimpleNamespace(preflight=lambda *args: None, failure_code=scope.failure_code))
    spec = importlib.util.spec_from_file_location('recovery_seed_owner', P.parent / 'seed.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    owner = module.Owner.__new__(module.Owner)
    owner.ns = 'feature-sit-test-1483-sit'
    owner.check_namespace = lambda: None
    owner.k = lambda *args: {'items': []}
    host = 'web-saas-postgresql.feature-sit-test-1483-sit.svc.cluster.local'
    owner.secret = lambda name, fields: {'NATIVE_TARGET_DSN': 'postgresql://xc_schema_owner:' + 'a'*64 + '@' + host + ':5432/account?sslmode=disable'}
    roles = ['xc_schema_owner', 'xc_accounts_rw', 'xc_billing_rw', 'xc_lane_audit']
    def psql(query):
        if 'FROM pg_class c' in query: return '0\n0\n0'
        if 'SELECT rolname' in query: return '\n'.join(roles)
        if 'pg_auth_members' in query: return '0'
        if 'pg_database' in query or 'pg_namespace' in query: return 'xc_schema_owner'
        raise AssertionError(query)
    owner.psql = psql
    assert owner.validate_empty_recovery(host, {role: '' for role in roles}) == 'a'*64
    owner.k = lambda *args: {'items': [{'status': {'active': 1}}]}
    with pytest.raises(module.Blocked, match='active jobs'):
        owner.validate_empty_recovery(host, {role: '' for role in roles})
    owner.k = lambda *args: {'items': []}
    owner.psql = lambda query: '1\n0\n0' if 'FROM pg_class c' in query else psql(query)
    with pytest.raises(module.Blocked, match='schema objects'):
        owner.validate_empty_recovery(host, {role: '' for role in roles})
    owner.psql = lambda query: '1' if 'pg_auth_members' in query else psql(query)
    with pytest.raises(module.Blocked, match='memberships'):
        owner.validate_empty_recovery(host, {role: '' for role in roles})
    owner.psql = psql
    owner.secret = lambda name, fields: {'NATIVE_TARGET_DSN': 'postgresql://xc_schema_owner:' + 'a'*64 + '@other.example:5432/account?sslmode=disable'}
    with pytest.raises(module.Blocked, match='credential contract'):
        owner.validate_empty_recovery(host, {role: '' for role in roles})


def test_native_init_waits_for_postgres_from_same_pod_network(monkeypatch):
    import sys, types
    monkeypatch.setitem(sys.modules, 'contracts', c)
    monkeypatch.setitem(sys.modules, 'platform_scope', types.SimpleNamespace(preflight=lambda *args: None, failure_code=scope.failure_code))
    spec = importlib.util.spec_from_file_location('wait_seed_owner', P.parent / 'seed.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    owner = module.Owner.__new__(module.Owner)
    owner.ns = 'feature-sit-test-1483-sit'
    captured = []
    owner.apply = captured.append
    owner.raw = lambda *args, **kwargs: '{}'
    owner.job('native-init', 'accounts-image', ['migratectl'], wait_postgres='pinned-postgres-image')
    pod = captured[0]['spec']['template']['spec']
    assert pod['initContainers'][0]['image'] == 'pinned-postgres-image'
    assert 'pg_isready' in pod['initContainers'][0]['command'][2]
    assert pod['initContainers'][0]['env'][0]['value'] == 'web-saas-postgresql.feature-sit-test-1483-sit.svc.cluster.local'
    assert pod['automountServiceAccountToken'] is False


def test_native_continuation_requires_exact_successful_job_and_empty_business_data(monkeypatch):
    import copy, sys, types
    monkeypatch.setitem(sys.modules, 'contracts', c)
    monkeypatch.setitem(sys.modules, 'platform_scope', types.SimpleNamespace(preflight=lambda *args: None, failure_code=scope.failure_code))
    spec = importlib.util.spec_from_file_location('native_continuation_owner', P.parent / 'seed.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    owner = module.Owner.__new__(module.Owner)
    owner.ns = 'feature-sit-test-1483-sit'
    image = 'ghcr.io/ai-workspace-services/accounts@sha256:'+'a'*64
    sha = 'c'*64; uid = '1'*8+'-'+'1'*4+'-'+'1'*4+'-'+'1'*4+'-'+'1'*12
    owner.r = {'tagged_lane': {'feature_id': 'sit-test-1483'}, 'runtime_mapping': {'images': {'accounts': {'platform_manifest_digest': 'sha256:'+'b'*64}}}}
    cmd = ['/usr/local/bin/migratectl','init','--environment','sit','--dsn-env','NATIVE_TARGET_DSN','--schema-sha256',sha,'--writers-paused','--dry-run=false']
    env = [{'name':'NATIVE_TARGET_DSN','valueFrom':{'secretKeyRef':{'name':'web-saas-db-ddl','key':'NATIVE_TARGET_DSN'}}}]
    job = {'metadata': {'name':'dev-seed-native-init-1234','namespace':owner.ns,'uid':uid,'labels':{'seed-owner':module.OWNER}}, 'status': {'succeeded':1,'conditions':[{'type':'Complete','status':'True'}]}, 'spec': {'template':{'spec':{'containers':[{'image':image,'command':cmd,'env':env}]}}}}
    pod = {'metadata':{'ownerReferences':[{'uid':uid,'controller':True}]},'status':{'phase':'Succeeded','containerStatuses':[{'imageID':image,'state':{'terminated':{'exitCode':0}}}]}}
    owner.k = lambda *args: {'items': [job] if 'jobs' in args else [pod]}
    def psql(query):
        if 'SELECT tablename' in query: return 'one\ntwo\nschema_migrations'
        if 'SELECT version,dirty' in query: return '123|f'
        if 'pg_stat_activity' in query: return '0'
        if 'SELECT count(*) FROM public.' in query: return '0\n0'
        raise AssertionError(query)
    owner.psql = psql
    assert owner.validate_native_continuation(['one','two'], {'migration_version':123}, image, sha) == uid
    job['spec']['template']['spec']['containers'][0]['image'] = image.replace('a'*64,'d'*64)
    with pytest.raises(module.Blocked, match='unique authenticated'):
        owner.validate_native_continuation(['one','two'], {'migration_version':123}, image, sha)
    job['spec']['template']['spec']['containers'][0]['image'] = image
    owner.psql = lambda query: '1\n0' if 'SELECT count(*) FROM public.' in query else psql(query)
    with pytest.raises(module.Blocked, match='business data'):
        owner.validate_native_continuation(['one','two'], {'migration_version':123}, image, sha)


def test_initialized_native_recovery_skips_native_and_role_rotation(monkeypatch):
    import json, sys, types
    monkeypatch.setitem(sys.modules, 'contracts', c)
    monkeypatch.setitem(sys.modules, 'platform_scope', types.SimpleNamespace(preflight=lambda *args: None, failure_code=scope.failure_code))
    spec = importlib.util.spec_from_file_location('native_complete_owner', P.parent / 'seed.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sha = 'a'*40; image = 'ghcr.io/ai-workspace-services/postgresql@sha256:'+'e'*64
    tables = ['table_' + str(i) for i in range(78)]
    manifest = {'format':1, 'migration_version':123, 'schema_sha256':'d'*64, 'business_table_count':78, 'business_tables':tables}
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, *args): return json.dumps(manifest).encode()
    monkeypatch.setattr(module.urllib.request, 'urlopen', lambda *args,**kwargs: Response())
    monkeypatch.setattr(module, 'verify_tagged_sources', lambda *args: tables)
    owner = module.Owner.__new__(module.Owner)
    owner.ns = 'feature-sit-test-1483-sit'
    lane = {'feature_id':'sit-test-1483','feature_tag':'smoke-sit-test-1483','candidate_sha256':'b'*64}
    inputs = {'accounts_image':'accounts-image','accounts_sha':sha,'billing_sha':sha,'accounts_native_sha256':'d'*64,'business_table_count':78,'migration_version':123}
    owner.r = {'tagged_lane':lane, 'candidate_sha':sha,'stage':'sit','instance_id':'123','node_uid':'node','cluster_uid':'cluster','namespace_uid':'ns','data_baseline_sha256':'c'*64}
    owner.tagged_inputs = lambda: (inputs,image,'sha256:'+'f'*64)
    owner.check_namespace = lambda: None
    source = {'metadata':{'name':'fixed'},'spec':{'url':'https://github.com/ai-workspace-infra/gitops.git','ref':{'commit':sha}},'status':{'artifact':{'revision':'sha1:'+sha}}}
    def k(*args):
        if 'gitrepositories.source.toolkit.fluxcd.io' in args: return {'items':[source]}
        if 'gitrepository' in args: return source
        if 'secrets' in args: return {'items':[{'metadata':{'name':'web-saas-db-ddl'}},{'metadata':{'name':'web-saas-db-probe'}}]}
        if 'pod' in args: return {'spec':{'containers':[{'image':image}]},'status':{'containerStatuses':[{'imageID':image}]}}
        raise AssertionError(args)
    owner.k = k
    owner.raw = lambda *args,**kwargs: ''
    owner.validate_empty_recovery = lambda host,passwords,require_empty: 'e'*64
    native_checks = []
    owner.validate_native_continuation = lambda *args: native_checks.append(True) or 'prior-job-uid'
    owner.secret = lambda name,fields: ({'ACCOUNTS_PASSWORD':'1'*64,'BILLING_PASSWORD':'2'*64,'AUDIT_PASSWORD':'3'*64} if name == 'web-saas-db-probe' else fields)
    sql = []
    def psql(query, **kwargs):
        sql.append(query)
        if 'SHOW log_statement' in query: return 'none\n-1\n-1\n'
        if "count(*) FROM pg_tables WHERE schemaname NOT" in query: return '79'
        if 'SELECT tablename' in query: return '\n'.join(tables+['schema_migrations','cloud_vendor_costs'])
        if 'SELECT count(*)' in query: return '0'
        return ''
    owner.psql = psql
    jobs = []
    def job(purpose,*args,**kwargs):
        jobs.append(purpose)
        if purpose == 'manifest': return json.dumps(manifest)
        if purpose.startswith('probe-'):
            assert kwargs.get('wait_postgres') == image
            return 'identity_probes_verified\n'
        raise AssertionError('native init must not be repeated')
    owner.job = job
    result = owner.initialize()
    assert result['seed_recovery'] == 'verified_native_initialized' and result['prior_native_job_uid'] == 'prior-job-uid'
    assert native_checks == [True] and 'native-init' not in jobs
    assert not any('ALTER ROLE' in query or 'CREATE ROLE' in query for query in sql)
    assert any(query.startswith('BEGIN; SET LOCAL ROLE') and query.endswith('COMMIT;') for query in sql)


def test_tagged_seed_keeps_audit_view_outside_accounts_catalog_and_grants_minimal_cloud_read():
    tables = ['table_' + str(i) for i in range(78)]
    sql = c.grants(tables, expected_count=78, tagged=True)
    assert 'GRANT SELECT ON public.cloud_vendor_costs TO xc_accounts_rw;' in sql
    assert 'GRANT SELECT, INSERT, UPDATE, DELETE ON public.cloud_vendor_costs TO xc_accounts_rw' not in sql
    audit = c.audit_grants(tagged=True)
    assert 'CREATE SCHEMA lane_audit AUTHORIZATION xc_schema_owner' in audit
    assert 'CREATE VIEW lane_audit.dev_lane_schema_status' in audit
    assert 'CREATE VIEW public.dev_lane_schema_status' not in audit
    assert 'GRANT SELECT ON lane_audit.dev_lane_schema_status TO xc_lane_audit' in audit
    assert 'GRANT USAGE ON SCHEMA public TO xc_lane_audit' not in audit
    assert 'CREATE VIEW public.dev_lane_schema_status' in c.audit_grants()


def test_seeded_scope_repair_is_bound_idempotent_and_preserves_credentials(monkeypatch):
    import sys, types
    monkeypatch.setitem(sys.modules, 'contracts', c)
    monkeypatch.setitem(sys.modules, 'platform_scope', types.SimpleNamespace(preflight=lambda *args: None, failure_code=scope.failure_code))
    spec = importlib.util.spec_from_file_location('scope_repair_owner', P.parent / 'seed.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    owner = module.Owner.__new__(module.Owner)
    owner.ns = 'feature-sit-test-1483-sit'
    owner.r = {'tagged_lane':{'feature_id':'sit-test-1483','feature_tag':'smoke-sit-test-1483','candidate_sha256':'c'*64},
               'instance_id':'123','node_uid':'node','cluster_uid':'cluster','namespace_uid':'ns','candidate_sha':'a'*40,'data_baseline_sha256':'d'*64}
    owner.check_namespace = lambda: None
    owner.validate_empty_recovery = lambda host,passwords,require_empty: 'f'*64
    owner.validate_native_continuation = lambda *args,**kwargs: 'prior-job-uid'
    owner.k = lambda *args: {'status':{'artifact':{'revision':'sha1:'+'a'*40}}}
    probes = []
    owner.verify_role_identities = lambda *args: probes.append(args)
    writes = []
    def psql(query):
        if 'to_regclass' in query: return 'dev_lane_schema_status|none'
        if 'pg_get_viewdef' in query: return ' SELECT version,\n dirty\n FROM schema_migrations;'
        if 'SELECT c.relname' in query: return 'one\ntwo\nschema_migrations\ncloud_vendor_costs\ndev_lane_schema_status'
        if 'FROM pg_namespace' in query: return ''
        if 'FROM pg_class WHERE oid' in query: return 'xc_schema_owner\nxc_schema_owner'
        if query.startswith('BEGIN;'): writes.append(query); return ''
        raise AssertionError(query)
    owner.psql = psql
    result = owner.repair_seeded_scope(['one','two'], {}, 'image', 'd'*64, 'postgres', 'sha1:'+'a'*40, {'metadata':{'name':'source'}})
    assert result['result'] == 'verified' and result['seed_recovery'] == 'audit_scope_isolation'
    assert result['prior_native_job_uid'] == 'prior-job-uid' and len(probes) == 1
    assert 'ALTER VIEW public.dev_lane_schema_status SET SCHEMA lane_audit' in writes[0]
    assert 'GRANT SELECT ON public.cloud_vendor_costs TO xc_accounts_rw' in writes[0]
    assert 'ALTER ROLE' not in writes[0] and 'DROP ' not in writes[0]
    writes.clear()
    owner.psql = lambda query: 'unexpected' if 'pg_get_viewdef' in query else psql(query)
    with pytest.raises(module.Blocked, match='view definition'):
        owner.repair_seeded_scope(['one','two'], {}, 'image', 'd'*64, 'postgres', 'sha1:'+'a'*40, {'metadata':{'name':'source'}})
    assert not writes
    owner.r['tagged_lane']['feature_id'] = 'other'
    with pytest.raises(module.Blocked, match='outside the approved'):
        owner.repair_seeded_scope(['one','two'], {}, 'image', 'd'*64, 'postgres', 'sha1:'+'a'*40, {'metadata':{'name':'source'}})


def test_seeded_rehearsal_routes_to_scope_repair_without_reinitializing(monkeypatch):
    import json, sys, types
    monkeypatch.setitem(sys.modules, 'contracts', c)
    monkeypatch.setitem(sys.modules, 'platform_scope', types.SimpleNamespace(preflight=lambda *args: None, failure_code=scope.failure_code))
    spec = importlib.util.spec_from_file_location('seeded_scope_entry', P.parent / 'seed.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sha = 'a'*40; image = 'ghcr.io/ai-workspace-services/postgresql@sha256:'+'e'*64
    tables = ['table_' + str(i) for i in range(78)]
    manifest = {'format':1,'migration_version':123,'schema_sha256':'d'*64,'business_table_count':78,'business_tables':tables}
    class Response:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def read(self,*args): return json.dumps(manifest).encode()
    monkeypatch.setattr(module.urllib.request,'urlopen',lambda *args,**kwargs:Response())
    monkeypatch.setattr(module,'verify_tagged_sources',lambda *args:tables)
    owner = module.Owner.__new__(module.Owner)
    owner.ns = 'feature-sit-test-1483-sit'
    lane = {'feature_id':'sit-test-1483','feature_tag':'smoke-sit-test-1483','candidate_sha256':'b'*64}
    inputs = {'accounts_image':'accounts-image','accounts_sha':sha,'billing_sha':sha,'accounts_native_sha256':'d'*64,'business_table_count':78,'migration_version':123}
    owner.r = {'tagged_lane':lane,'candidate_sha':sha,'stage':'sit'}
    owner.tagged_inputs = lambda: (inputs,image,'sha256:'+'f'*64)
    owner.check_namespace = lambda: None
    source = {'metadata':{'name':'fixed'},'spec':{'url':'https://github.com/ai-workspace-infra/gitops.git','ref':{'commit':sha}},'status':{'artifact':{'revision':'sha1:'+sha}}}
    names = ['web-saas-db-ddl','web-saas-db-probe','web-saas-db-audit','web-saas-accounts-runtime','web-saas-billing-runtime','web-saas-console-runtime','web-saas-content-runtime']
    def k(*args):
        if 'gitrepositories.source.toolkit.fluxcd.io' in args: return {'items':[source]}
        if 'secrets' in args: return {'items':[{'metadata':{'name':name,'labels':{'seed-owner':module.OWNER}}} for name in names]}
        if 'pod' in args: return {'spec':{'containers':[{'image':image}]},'status':{'containerStatuses':[{'imageID':image}]}}
        raise AssertionError(args)
    owner.k = k
    owner.raw = lambda *args,**kwargs: ''
    owner.psql = lambda q,**kwargs: 'none\n-1\n-1\n' if 'SHOW log_statement' in q else '80'
    calls = []
    owner.job = lambda purpose,*args,**kwargs: json.dumps(manifest) if purpose == 'manifest' else pytest.fail('must not reinitialize')
    owner.repair_seeded_scope = lambda *args: calls.append(args) or {'result':'verified','seed_recovery':'audit_scope_isolation'}
    assert owner.initialize()['seed_recovery'] == 'audit_scope_isolation'
    assert len(calls) == 1
