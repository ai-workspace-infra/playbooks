"""Synthetic contracts; no host, registry, source or database access."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

OWNER = Path(__file__).resolve().parents[2] / 'scripts/data_operations/selfhost'
sys.path.insert(0, str(OWNER))
import managed_runtime_host as HOST
import native_init_host as PRIVATE
sys.path.pop(0)


def spec():
    services = {}
    for name, (repo, _, _) in HOST.SERVICES.items():
        sha = ('a' if name == 'accounts' else 'b') * 40
        services[name] = dict(commit=sha, image='ghcr.io/ai-workspace-services/'+repo+':sha-'+sha,
                              image_digest='sha256:'+'c'*64)
    return dict(schema=1, environment='prod', host='web-saas-prod', services=services)


def state(name):
    service = spec()['services'][name]
    argv = HOST.standby_argv(name, service, 'managed-runtime-probe-'+'a'*32)
    env = [argv[i+1] for i, arg in enumerate(argv) if arg == '--env']
    return dict(State=dict(Running=True), Mounts=[], Config=dict(
        Image=service['image']+'@'+service['image_digest'], Entrypoint=[HOST.SERVICES[name][1]],
        Cmd=['--config','/dev/null'] if name == 'accounts' else None, Env=env,
        Labels={'io.xworktech.owner':'playbooks-managed-runtime-probe'}),
        HostConfig=dict(NetworkMode='none', RestartPolicy=dict(Name='no'), ReadonlyRootfs=True,
                        CapDrop=['ALL'], SecurityOpt=['no-new-privileges'], PortBindings={}))


class ManagedRuntimeTests(unittest.TestCase):
    def test_contract_requires_both_services_exact_image_and_no_runtime_override(self):
        HOST.validate_spec(spec())
        cases = []
        for key, value in [('schema',2),('environment','uat'),('host','other')]:
            v=spec();v[key]=value;cases.append(v)
        v=spec();v['services'].pop('billing');cases.append(v)
        for key,value in [('commit','short'),('image','local:image'),('image_digest','mutable'),('role','primary')]:
            v=spec();v['services']['accounts'][key]=value;cases.append(v)
        v=spec();v['database_url']='forbidden';cases.append(v)
        for value in cases:
            with self.assertRaises(HOST.Refused):HOST.validate_spec(value)

    def test_standby_launcher_overrides_legacy_entrypoint_and_has_no_password(self):
        self.assertEqual(HOST.PROBE_IDENTITY,hashlib.sha256(
            b'{"Host":"127.0.0.1","Port":1,"Database":"account","Role":"managed_probe"}').hexdigest())
        for name,service in spec()['services'].items():
            argv=HOST.standby_argv(name,service,'managed-runtime-probe-'+'a'*32)
            self.assertEqual(argv[argv.index('--network')+1],'none')
            self.assertEqual(argv[argv.index('--entrypoint')+1],HOST.SERVICES[name][1])
            self.assertIn('DATABASE_BACKGROUND_WRITERS=false',argv)
            self.assertIn('DATABASE_RUNTIME_ROLE=standby',argv)
            self.assertIn('DATABASE_URL='+HOST.PROBE_DSN,argv)
            self.assertNotIn('--env-file',argv)
            self.assertNotIn('--publish',argv)
            self.assertNotIn('--mount',argv)
            self.assertNotIn('entrypoint.sh',str(argv))
            self.assertNotIn('PASSWORD',str(argv))
            if name=='accounts':self.assertEqual(argv[-2:],['--config','/dev/null'])
        with self.assertRaises(HOST.Refused):HOST.standby_argv('accounts',spec()['services']['accounts'],'existing-app')

    def test_actual_container_security_and_release_mismatch_refused(self):
        for name in HOST.SERVICES:
            current=state(name)
            with patch.object(HOST,'command',return_value=json.dumps([current])):
                HOST.inspect_container(name,spec()['services'][name],'fixture')
            cases=[]
            for section,key,value in [('HostConfig','NetworkMode','host'),('HostConfig','ReadonlyRootfs',False),
                                     ('HostConfig','CapDrop',[]),('HostConfig','PortBindings',{'8080/tcp':[]}),
                                     ('Config','Image','other'),('Config','Entrypoint',['/bin/sh'])]:
                v=deepcopy(current);v[section][key]=value;cases.append(v)
            v=deepcopy(current);v['Mounts']=[{'Source':'secret'}];cases.append(v)
            v=deepcopy(current);v['Config']['Env']+=['POSTGRES_PASSWORD=synthetic'];cases.append(v)
            v=deepcopy(current);v['Config']['Env']=[x.replace('DATABASE_RUNTIME_ROLE=standby','DATABASE_RUNTIME_ROLE=primary') for x in v['Config']['Env']];cases.append(v)
            for value in cases:
                with patch.object(HOST,'command',return_value=json.dumps([value])):
                    with self.assertRaises(HOST.Refused):HOST.inspect_container(name,spec()['services'][name],'fixture')

    def test_http_exchange_has_only_loopback_requests_and_parses_503(self):
        with patch.object(HOST,'command',return_value='HTTP/1.1 503 Service Unavailable\nContent-Type: application/json\n\n{"reason":"database_runtime_standby"}') as run:
            self.assertEqual(HOST.probe('fixture','8080','/api/users','DELETE'),(503,dict(reason='database_runtime_standby')))
            argv=run.call_args.args[0]
            self.assertEqual(argv,['docker','exec','-i','fixture','nc','-w','5','127.0.0.1','8080'])
            self.assertIn('DELETE /api/users HTTP/1.1',run.call_args.kwargs['input'])
        for port,path,method in [('5432','/api/users','GET'),('8080','/admin/reset','GET'),('8080','/api/users','CONNECT')]:
            with self.assertRaises(HOST.Refused):HOST.probe('fixture',port,path,method)

    def test_probe_metadata_readiness_and_all_verbs_required(self):
        service=spec()['services']['accounts']
        body=dict(image=service['image'],tag='sha-'+service['commit'],commit=service['commit'],database_role='standby',
                  bootstrap_writes=False,proxy_uuid_rotator=False,background_writers=False,business_requests_enabled=False,
                  configured_database_sha256=HOST.PROBE_IDENTITY,schema_version=0,schema_management='external')
        def response(container,port,path,method='GET'):
            if path=='/api/users':return 503,dict(reason='database_runtime_standby')
            return (503 if path=='/readyz' else 200),body
        with patch.object(HOST,'probe',side_effect=response) as probe:
            HOST.verify_probes('accounts',service,'fixture')
            self.assertEqual({c.args[3] for c in probe.call_args_list if len(c.args)==4}, {'GET','POST','PUT','PATCH','DELETE'})
        for key,value in [('commit','wrong'),('database_role','primary'),('background_writers',True),('schema_version',2026100701)]:
            altered=dict(body);altered[key]=value
            with patch.object(HOST,'probe',return_value=(200,altered)):
                with self.assertRaises(HOST.Refused):HOST.verify_probes('accounts',service,'fixture')

    def test_owned_short_lived_container_always_removed(self):
        for fail in (False,True):
            with patch.object(HOST,'command'), patch.object(HOST,'inspect_container'), \
                 patch.object(HOST,'verify_probes',side_effect=HOST.Refused('fixture') if fail else None), \
                 patch.object(HOST,'remove_execution_container') as remove:
                if fail:
                    with self.assertRaises(HOST.Refused):HOST.qualify('accounts',spec()['services']['accounts'],{})
                else:HOST.qualify('accounts',spec()['services']['accounts'],{})
                self.assertEqual(remove.call_count,1)
                self.assertRegex(remove.call_args.args[0],'^managed-runtime-probe-[0-9a-f]{32}$')

    def test_only_registry_credentials_apply_gate_and_unaccepted_receipt(self):
        creds=dict(ghcr_username='fixture',ghcr_token='synthetic')
        PRIVATE.validate_registry_credentials(creds)
        for value in ({},dict(creds,postgres_password='forbidden'),dict(creds,ghcr_token='bad\nline')):
            with self.assertRaises(HOST.Refused):PRIVATE.validate_registry_credentials(value)
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaises(HOST.Refused):HOST.execute(spec(),False,creds)
            with self.assertRaises(HOST.Refused):HOST.execute(spec(),'false',creds)
        with patch.dict(os.environ,MANAGED_RUNTIME_GATE_VERIFIED='true'), \
             patch.object(HOST,'registry_session') as session, patch.object(HOST,'command'), patch.object(HOST,'qualify') as qualify:
            session.return_value.__enter__.return_value={}
            receipt=HOST.execute(spec(),False,creds)
            self.assertEqual(qualify.call_count,2)
            self.assertEqual(receipt['stage'],'managed_images_qualified')
            for key in ('database_connected','schema_verified','application_deployed','database_cutover_approved'):
                self.assertIs(receipt[key],False)
            qualify.reset_mock()
            self.assertEqual(HOST.execute(spec(),True,creds)['stage'],'managed_image_preview')
            qualify.assert_not_called()

    def test_action_and_role_canonical_access_without_database_secret(self):
        root=OWNER.parents[2]
        action=(root/'.github/actions/prod-managed-runtime/action.yml').read_text()
        role=(root/'roles/web_saas_managed_runtime_qualification/tasks/main.yml').read_text()
        runner=(OWNER/'managed_runtime_runner.sh').read_text()
        playbook=(root/'qualify-web-saas-managed-runtime.yml').read_text()
        for content in (action,role,runner,playbook):
            self.assertNotIn('POSTGRES_PASSWORD',content)
            self.assertNotIn('SUPABASE_CONNECT',content)
            self.assertNotIn('source_dsn',content)
        self.assertIn('stdin_add_newline: true',role)
        self.assertIn('native_access_guard.sh',runner)
        self.assertIn('[[ "$MANAGED_RUNTIME_DRY_RUN" == true ||',runner)
        self.assertIn('native_authoritative_cmdb.environment',playbook)
        self.assertIn('native_writer_guard_host.sh',playbook)
        self.assertIn('same-run',action)
        self.assertIn('.database_cutover_approved == false',runner)


if __name__=='__main__':unittest.main()
