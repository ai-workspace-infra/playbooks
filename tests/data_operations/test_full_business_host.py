"""Credential-free owner gates; mocks are not production acceptance."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
OWNER=ROOT/'scripts/data_operations/selfhost'
sys.path.insert(0,str(OWNER))
import full_business_host as HOST
import full_business_target_diagnostic as DIAGNOSTIC
sys.path.pop(0)


def source_identity():
    identity={'Host':'aws-0-ap-southeast-1.pooler.supabase.com','Port':5432,'Database':'postgres',
        'Role':'postgres.abcdefghijklmnopqrst'}
    return hashlib.sha256(json.dumps(identity,separators=(',',':')).encode()).hexdigest()


def spec():
    tables=sorted(['users']+['fixture_'+str(i).zfill(2) for i in range(51)])
    initial=dict(schema=1,environment='prod',host='web-saas-prod',database='account',accounts_commit='a'*40,
        image='ghcr.io/ai-workspace-services/accounts:sha-'+'a'*40,image_digest='sha256:'+'b'*64,
        schema_sha256=HOST.ACCOUNTS_SQL_SHA256,migration_version=2026100601,business_table_count=52,business_tables=tables)
    transfer=dict(schema=1,environment='prod',host='web-saas-prod',database='account',accounts_commit='c'*40,
        image='ghcr.io/ai-workspace-services/accounts:sha-'+'c'*40,image_digest='sha256:'+'d'*64,
        schema_sha256=HOST.ACCOUNTS_SQL_SHA256,billing_schema_sha256=HOST.BILLING_SQL_SHA256,
        migration_version=2026100701,business_tables=sorted(tables+['cloud_vendor_costs']),batch_size=1000,
        database_cutover_approved=False)
    return dict(initialization=initial,transfer=transfer,source=dict(ready=False,role='serverless_supabase',endpoint='session_pooler',
        project_ref='abcdefghijklmnopqrst',
        tls_required=True,identity_sha256=None,direction='prod-supabase-to-prod-selfhost'))


def credentials():
    return dict(postgres_password='fictional$@:',source_dsn='postgresql://postgres.abcdefghijklmnopqrst:'
        'fictional@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres',
        ghcr_username='fixture-user',ghcr_token='fictional-registry-value')


def receipt(contract,mode):
    return dict(format=1,environment='prod',result={'preview':'eligible','copy':'copied','compare':'equal'}[mode],
        schema_sha256=HOST.ACCOUNTS_SQL_SHA256,billing_schema_sha256=HOST.BILLING_SQL_SHA256,
        migration_version=2026100701,batch_size=1000,source_identity_sha256=source_identity(),
        source_snapshot_sha256='e'*64,source_catalog_sha256='f'*64,source_read_only=True,source_table_count=44,
        user_count=1,full_business_equal=mode!='preview',target_writes=mode=='copy',database_cutover_approved=False,
        core_users=dict(source=dict(count=1,email_sha256='2'*64,password_hash_sha256='3'*64,email_proxy_sha256='4'*64),
            target=dict(count=0,email_sha256='',password_hash_sha256='',email_proxy_sha256='') if mode=='preview' else
            dict(count=1,email_sha256='2'*64,password_hash_sha256='3'*64,email_proxy_sha256='4'*64)),
        snapshot_started_at='2026-10-07T00:00:00Z',completed_at='2026-10-07T00:01:00Z',
        tables={} if mode=='preview' else {t:dict(rows=1 if t=='users' else 0,sha256='1'*64)
            for t in contract['transfer']['business_tables']})


class FullBusinessOwnerTests(unittest.TestCase):
    def setUp(self):
        self.spec=spec();self.credentials=credentials()

    def test_target_diagnostic_reads_only_metadata_and_never_emits_container_names(self):
        with patch.object(DIAGNOSTIC,'command',return_value='web-saas-postgresql\nweb-saas-app'), \
             patch.object(DIAGNOSTIC,'sql',side_effect=['170006','1','2026100701:false','53']) as sql, \
             patch('builtins.print') as output:
            DIAGNOSTIC.main()
        data=json.loads(output.call_args.args[0])
        self.assertEqual(data['active_writer_container_count'],1)
        self.assertEqual(data['business_table_count'],53)
        self.assertFalse(data['source_accessed']);self.assertFalse(data['target_writes'])
        self.assertNotIn('web-saas-app',output.call_args.args[0])
        for call in sql.call_args_list:
            self.assertNotIn('FROM public.users',call.args[0])

    def test_target_diagnostic_forces_readonly_transaction(self):
        with patch.object(DIAGNOSTIC,'command',return_value='17') as command:
            DIAGNOSTIC.sql('SHOW server_version_num')
        self.assertEqual(command.call_args.args[0][-1],
            'BEGIN READ ONLY; SHOW server_version_num; COMMIT;')

    def execute(self,mode,fail=False):
        calls=[];configs=[]
        def command(argv,**kw):
            calls.append((argv,kw))
            if argv[0]=='findmnt':return 'tmpfs'
            return ''
        def private(argv,**kw):
            calls.append((argv,kw))
            configs.append(Path(kw['env']['DOCKER_CONFIG']))
            self.assertEqual(configs[-1].stat().st_mode & 0o777,0o700)
            self.assertNotIn('fictional',str(argv))
            self.assertNotIn('NATIVE_SOURCE_DSN',kw['env'])
            self.assertNotIn('NATIVE_TARGET_DSN',kw['env'])
            if argv[-1]=='native-schema':
                return json.dumps({'format':1,**{k:self.spec['initialization'][k] for k in
                    ('schema_sha256','migration_version','business_table_count','business_tables')}})
            if '--source-dsn-env=NATIVE_SOURCE_DSN' in argv:
                self.assertEqual(kw['input'].count('\n'),2)
                self.assertIn('fictional%24%40%3A',kw['input'])
                self.assertIn('sslmode=require',kw['input'])
                self.assertIn('default_transaction_read_only=on',kw['input'])
                self.assertNotIn('--env-file',argv)
                if fail:raise HOST.native.Refused('fictional timeout')
                data=receipt(self.spec,mode);data['private_extra']='do-not-republish'
                return json.dumps(data)
            return ''
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,NATIVE_DATA_GATE_VERIFIED='true'), \
             patch.object(HOST.native,'command',side_effect=command),patch.object(HOST,'run_private',side_effect=private), \
             patch.object(HOST,'verify_storage'),patch.object(HOST,'verify_target') as target, \
             patch.object(HOST.native,'remove_execution_container') as cleanup:
            original=HOST.tempfile.TemporaryDirectory
            # Real private directory lifecycle in a local temp fixture, not /dev/shm.
            with patch.object(HOST.tempfile,'TemporaryDirectory',side_effect=lambda **kw:original(dir=tmp)):
                if fail:
                    with self.assertRaises(HOST.native.Refused):HOST.execute(self.spec,OWNER,mode,self.credentials)
                    cleanup.assert_called_once()
                    result=None
                else:
                    result=HOST.execute(self.spec,OWNER,mode,self.credentials)
                    cleanup.assert_called_once()
            for path in configs:self.assertFalse(path.exists())
            if not fail:
                self.assertEqual(target.call_args.kwargs['require_empty'],mode=='preview')
        return calls,result

    def test_preview_uses_only_metadata_cli_and_sanitized_receipt(self):
        calls,data=self.execute('preview')
        self.assertFalse(data['target_writes']);self.assertFalse(data['full_business_equal'])
        self.assertEqual(data['tables'],{})
        self.assertIn('--dry-run=true',str([c[0] for c in calls]))
        self.assertNotIn('private_extra',data)

    def test_copy_and_compare_leave_source_freeze_and_cutover_unapproved(self):
        for mode in ('copy','compare'):
            with self.subTest(mode=mode):
                calls,data=self.execute(mode)
                self.assertEqual(len(data['tables']),53)
                self.assertTrue(data['full_business_equal'])
                self.assertEqual(data['target_writes'],mode=='copy')
                self.assertFalse(data['source_writers_paused']);self.assertFalse(data['final_catchup_complete'])
                self.assertFalse(data['database_cutover_approved'])
                self.assertIn('--dry-run=false',str([c[0] for c in calls]))
                self.assertNotIn('reset',str([c[0] for c in calls]))

    def test_core_users_accepts_only_identity_contract_and_uses_dedicated_command(self):
        r=receipt(self.spec,'copy')
        r.update(scope='core_users', source_table_count=1, source_snapshot_sha256='', source_catalog_sha256='', tables={})
        safe=HOST.validate_receipt(r,self.spec,'core_users',source_identity())
        self.assertEqual(safe['scope'],'core_users')
        self.assertEqual(safe['tables'],{})
        self.assertEqual(HOST.migration_args(self.spec,'core_users')[0],'copy-core-users')

    def test_timeout_stops_owned_container_and_removes_registry_config(self):
        self.execute('copy',fail=True)
        diagnostic=HOST.failure_receipt()
        self.assertEqual(diagnostic, dict(format=1,result='failed',failure_stage='migration',
            database_cutover_approved=False))
        self.assertNotIn('fictional',json.dumps(diagnostic))

    def test_target_preflight_failure_identifies_stage_before_registry_or_source(self):
        with patch.dict(os.environ,NATIVE_DATA_GATE_VERIFIED='true'), \
             patch.object(HOST.native,'command'),patch.object(HOST,'verify_storage'), \
             patch.object(HOST,'verify_target',side_effect=HOST.native.Refused('private-row-and-password')), \
             patch.object(HOST,'run_private') as private:
            with self.assertRaises(HOST.native.Refused):
                HOST.execute(self.spec,OWNER,'preview',self.credentials)
            private.assert_not_called()
        self.assertEqual(HOST.failure_receipt()['failure_stage'],'target_schema')
        self.assertNotIn('private-row',json.dumps(HOST.failure_receipt()))

    def test_independent_gate_before_host_or_source(self):
        with patch.dict(os.environ,NATIVE_DATA_GATE_VERIFIED='false'),patch.object(HOST.native,'command') as command:
            with self.assertRaises(HOST.native.Refused):HOST.execute(self.spec,OWNER,'preview',self.credentials)
            command.assert_not_called()

    def test_unready_source_and_wrong_image_scope_are_refused(self):
        pending=copy.deepcopy(self.spec);pending['source'].update(ready=False,identity_sha256=None)
        HOST.validate_spec(pending)
        for field,value in [('ready',None),('role','postgres'),('tls_required',False),('identity_sha256','invalid'),
                            ('direction','selfhost-to-prod-supabase')]:
            candidate=copy.deepcopy(self.spec);candidate['source'][field]=value
            with self.assertRaises((HOST.native.Refused,TypeError)):HOST.validate_spec(candidate)
        for field,value in [('accounts_commit','main'),('schema_sha256','0'*64),('migration_version',99),('batch_size',50),('database_cutover_approved',True)]:
            candidate=copy.deepcopy(self.spec);candidate['transfer'][field]=value
            with self.assertRaises(HOST.native.Refused):HOST.validate_spec(candidate)

    def test_source_admin_wrong_project_transaction_pooler_and_tls_refused(self):
        value=self.credentials['source_dsn']
        identity,secure=HOST.validate_source_dsn(value,self.spec['source'])
        self.assertEqual(identity,source_identity());self.assertIn('sslmode=require',secure)
        self.assertIn('default_transaction_read_only=on',secure)
        readonly_value=value.replace('postgres.abcdefghijklmnopqrst:fictional',
            'readonly_release.abcdefghijklmnopqrst:fictional')
        _,readonly_secure=HOST.validate_source_dsn(readonly_value,self.spec['source'])
        self.assertIn('sslmode=require',readonly_secure)
        for dsn in [value.replace('postgres.abcdefghijklmnopqrst:fictional','admin:fictional'),value.replace(':5432/',':6543/'),
            value+'?sslmode=prefer',value.replace('abcdefghijklmnopqrst','aaaaaaaaaaaaaaaaaaaa'),
            value.replace('/postgres','/account'),value.replace('aws-0-ap-southeast-1.pooler.supabase.com',
                'db.abcdefghijklmnopqrst.supabase.co'),value+'\n']:
            with self.assertRaises(HOST.native.Refused):HOST.validate_source_dsn(dsn,self.spec['source'])

    def test_source_connection_readonly_cannot_be_disabled_by_startup_options(self):
        value=self.credentials['source_dsn']
        for query in ('default_transaction_read_only=off',
            'default_transaction_read_only=on&default_transaction_read_only=off',
            'options=-c+default_transaction_read_only%3Doff'):
            with self.assertRaises(HOST.native.Refused):
                HOST.validate_source_dsn(value+'?'+query,self.spec['source'])
        _,secure=HOST.validate_source_dsn(value+'?default_transaction_read_only=on',self.spec['source'])
        self.assertEqual(secure.count('default_transaction_read_only'),1)

    def test_preview_partial_scope_wrong_source_or_extra_private_data(self):
        for key,value in [('source_identity_sha256','0'*64),('source_read_only',False),('full_business_equal',True),('target_writes',True)]:
            r=receipt(self.spec,'preview');r[key]=value
            with self.assertRaises(HOST.native.Refused):HOST.validate_receipt(r,self.spec,'preview',source_identity())
        r=receipt(self.spec,'copy');del r['tables']['cloud_vendor_costs']
        with self.assertRaises(HOST.native.Refused):HOST.validate_receipt(r,self.spec,'copy',source_identity())
        r=receipt(self.spec,'copy');r['tables']['users']['rows']=0
        with self.assertRaises(HOST.native.Refused):HOST.validate_receipt(r,self.spec,'copy',source_identity())

    def test_role_pipelining_stdin_and_single_owner(self):
        role=(ROOT/'roles/web_saas_full_business_transfer/tasks/main.yml').read_text()
        self.assertIn('stdin:',role);self.assertIn('no_log: true',role);self.assertIn('  always:',role)
        self.assertNotIn('PROD_SUPABASE_READONLY_DSN:',role)
        runner=(OWNER/'full_business_runner.sh').read_text()
        self.assertIn('ANSIBLE_PIPELINING=true',runner);self.assertIn('native_access_guard.sh',runner)
        self.assertIn('test -s "$NATIVE_RECEIPT_FILE"',runner)
        self.assertIn('FULL_BUSINESS_MODE" == core_users',runner)
        self.assertIn("FULL_BUSINESS_MODE') in ['preview', 'copy', 'compare', 'core_users']",role)
        for bad in ('gcloud','terraform','createdb','pg_dump','--env-file'):self.assertNotIn(bad,runner)
        action=(ROOT/'.github/actions/prod-full-business/action.yml').read_text()
        self.assertIn('source_dsn:',action);self.assertNotIn('workflow_dispatch',action)
        args=HOST.migration_args(self.spec,'copy')
        self.assertEqual(args[0],'copy-full-business');self.assertNotIn('--dsn=',str(args))


if __name__=='__main__':unittest.main()
