"""Opt-in PostgreSQL17 plan-semantic fixture. Existing Docker image; no production driver.
SQL travels via stdin; only PLACEHOLDER_ONLY passwords; container data/logs not persisted.
"""
from pathlib import Path
import json,os,subprocess,tempfile,time,uuid
from database_contract import require,Rejected
from roles_adapter import make_plan

def run():
    checks=[];container=None
    spec=json.loads((Path(__file__).parent/'example.json').read_text())
    scope={'apiVersion':'DatabaseGrantScope/v1','schemas':['fixture_business']}
    snapshot={'lookup_status':200,'role_states':{},'credential_available':{}}
    plan=make_plan(spec,scope,snapshot)
    with tempfile.TemporaryDirectory(prefix='db-plan-fixture-') as td:
        env={'PATH':os.environ['PATH'],'HOME':td,'DOCKER_CONFIG':td+'/docker'};Path(env['DOCKER_CONFIG']).mkdir()
        def docker(*args,input=None,check=True):
            proc=subprocess.run(['docker',*args],input=input,text=True,capture_output=True,env=env)
            if check:require(proc.returncode==0,'fixture_docker_failed')
            return proc
        try:
            cmd="initdb -D /fixture/data -U fixture_admin --auth-local=trust --auth-host=reject >/dev/null && exec postgres -D /fixture/data -c listen_addresses='' -c unix_socket_directories=/fixture -c log_statement=none -c log_min_error_statement=panic -c log_min_messages=panic -c fsync=off"
            container=docker('run','--rm','--pull=never','--network','none','-d','--name','db-plan-fixture-'+uuid.uuid4().hex[:10],'--read-only','--user','999:999','--cap-drop=ALL','--security-opt','no-new-privileges','--log-driver','none','--tmpfs','/fixture:rw,noexec,nosuid,size=192m,uid=999,gid=999,mode=0700','--entrypoint','bash','postgres:17','-c',cmd).stdout.strip()
            require(len(container)==64,'fixture_id_invalid')
            ready=False
            for _ in range(100):
                if docker('exec',container,'pg_isready','-h','/fixture','-U','fixture_admin',check=False).returncode==0:ready=True;break
                time.sleep(.1)
            require(ready,'fixture_start_timeout')
            def sql(value,database='example',user='fixture_admin',check=True):
                return docker('exec','-i',container,'psql','-X','-q','-A','-t','-h','/fixture','-U',user,'-d',database,'-v','ON_ERROR_STOP=1',input=value,check=check)
            sql('CREATE DATABASE example;',database='postgres')
            sql("CREATE SCHEMA fixture_business; CREATE TABLE fixture_business.fixture_table (id int, v text); INSERT INTO fixture_business.fixture_table VALUES (1,'FIXTURE_ONLY');")
            statements=[]
            for a in plan.actions:
                query=a.sql+(' PASSWORD \'PLACEHOLDER_ONLY\'' if a.kind=='create_login' else '')
                statements.append(query+';')
            batch='BEGIN;\n'+'\n'.join(statements)+'\nCOMMIT;'
            sql(batch)
            require(sql("SELECT rolcanlogin FROM pg_roles WHERE rolname='example_owner';").stdout.strip()=='f','owner_login')
            checks.append('seven_role_plan_executes_owner_NOLOGIN')
            for purpose in ('readonly_export','readonly_audit','monitor','runtime'):
                user='example_'+purpose
                if purpose!='monitor':require(sql('SELECT count(*) FROM fixture_business.fixture_table;',user=user).stdout.strip()=='1','read_failed')
                for query in ['CREATE TABLE fixture_business.denied (id int);','SET ROLE example_owner;']:
                    require(sql(query,user=user,check=False).returncode!=0,'unexpected_privilege')
                if purpose.startswith('readonly') or purpose=='monitor':
                    require(sql("INSERT INTO fixture_business.fixture_table VALUES (2,'FIXTURE_ONLY');",user=user,check=False).returncode!=0,'readonly_write_allowed')
                else:sql("INSERT INTO fixture_business.fixture_table VALUES (2,'FIXTURE_ONLY');",user=user)
            checks.append('runtime_DML_and_readonly_export_audit_monitor_mutation_denials')
            require(sql("SELECT pg_has_role(current_user,'pg_monitor','USAGE');",user='example_monitor').stdout.strip()=='t','monitor_inherit_failed')
            checks.append('monitor_inherits_pg_monitor')
            sql('SET ROLE example_owner; CREATE TABLE fixture_business.future_table (id int);',user='example_ddl_migrator')
            sql('SELECT * FROM fixture_business.future_table;',user='example_readonly_audit')
            checks.append('migrator_explicit_SET_owner_future_default_ACL')
            # Runtime change rollback demonstrated with generated SQL, not a production connection factory.
            rollback='BEGIN; CREATE ROLE fixture_should_rollback NOLOGIN; SELECT 1/0; COMMIT;'
            require(sql(rollback,check=False).returncode!=0,'error_expected')
            require(sql("SELECT count(*) FROM pg_roles WHERE rolname='fixture_should_rollback';").stdout.strip()=='0','rollback_failed')
            checks.append('transaction_failure_rolls_back_role_creation')
            existing={'lookup_status':200,'role_states':{},'credential_available':{}}
            for r in spec['roles']:
                membership=['example_owner'] if r['purpose'] in ('dba','ddl_migrator') else (['pg_monitor'] if r['purpose']=='monitor' else [])
                existing['role_states'][r['name']]={'login':r['login'],'elevated':False,'memberships':membership}
                if r['login']:existing['credential_available'][r['purpose']]=True
            rerun=make_plan(spec,scope,existing)
            require(not any(a.kind=='create_login' or 'PASSWORD' in a.sql for a in rerun.actions),'rerun_rotation')
            sql('BEGIN;\n'+';\n'.join(a.sql for a in rerun.actions)+';\nCOMMIT;')
            checks.append('existing_role_plan_has_no_password_change')
        finally:
            if container:
                result=docker('stop','--time','3',container,check=False)
                require(result.returncode==0,'fixture_cleanup_failed')
                checks.append('created_container_removed_tmpfs_no_host_volume')
    return {'scope':'isolated_sql_plan_semantics_only','checks':checks,'production_acceptance':False,'credential_authentication':'not_tested_unix_socket_fixture_trust','tls':'not_tested_network_none'}
if __name__=='__main__':
    try:result=run()
    except Exception as exc:
        print(json.dumps({'status':'failed','code':str(exc) if isinstance(exc,Rejected) else 'fixture_failed'}));raise SystemExit(1)
    print(json.dumps(result,indent=2))
