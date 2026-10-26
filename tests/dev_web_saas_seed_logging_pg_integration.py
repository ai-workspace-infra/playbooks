"""Owned temporary PG17: force credential SQL error; never print server logs."""
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import types

ROOT = Path(__file__).resolve().parents[1]
B = Path(os.environ['PG17_BIN'])
root = Path(tempfile.mkdtemp(prefix='dev-seed-log-pg-'))
root.chmod(0o700)
sock = socket.socket(); sock.bind(('127.0.0.1',0)); port = sock.getsockname()[1]; sock.close()
def run(args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, text=True, capture_output=True, **kwargs)
try:
    run([B/'initdb','-D',root/'data','--auth-local=trust','--auth-host=scram-sha-256'])
    run([B/'pg_ctl','-D',root/'data','-l',root/'server.log','-o',f'-k {root} -p {port} -h 127.0.0.1','start'])
    env={**os.environ,'PGHOST':str(root),'PGPORT':str(port)}
    run([B/'psql','-XAtq','-d','postgres','-v','ON_ERROR_STOP=1'], env=env,
        input='CREATE ROLE lane_admin LOGIN SUPERUSER; CREATE DATABASE account; CREATE ROLE conflict_probe;')
    # Import the actual execution owner; test only its management SQL connection.
    sys.path.insert(0,str(ROOT/'roles/platform/dev_web_saas_seed/files'))
    sys.modules['platform_scope']=types.SimpleNamespace(preflight=lambda *args: None)
    import seed
    owner=seed.Owner.__new__(seed.Owner); owner.ns='feature-xconnect-referral-sit'
    def raw(*args,data=None):
        command=list(args[args.index('--')+1:])
        command[command.index('psql')]=str(B/'psql')
        return run(command, env=env, input=data).stdout
    owner.raw=raw
    baseline=run([B/'psql','-XAtq','-d','account','-c','SHOW log_min_error_statement;'],env=env).stdout.strip()
    assert baseline=='error'
    control_sentinel='synthetic_control_' + secrets.token_hex(24)
    try:
        owner.psql("CREATE ROLE conflict_probe LOGIN PASSWORD '"+control_sentinel+"';")
    except subprocess.CalledProcessError:
        pass
    else:
        raise AssertionError('default logging control conflict did not fail')
    sentinel='synthetic_' + secrets.token_hex(24)
    try:
        owner.psql("CREATE ROLE conflict_probe LOGIN PASSWORD '"+sentinel+"';",sensitive=True)
    except subprocess.CalledProcessError:
        pass
    else:
        raise AssertionError('forced role conflict did not fail')
    # Check effective protection in the very same implementation path.
    settings=owner.psql('SHOW log_min_error_statement; SHOW log_statement;',sensitive=True).split()
    assert settings==['panic','none']
    # A detected audit hook blocks before transmitting the password statement.
    def audit_raw(*args,data=None):
        command=list(args[args.index('--')+1:])
        command[command.index('psql')]=str(B/'psql')
        command[1] += ' -c pgaudit.log=all'
        return run(command, env=env, input=data).stdout
    owner.raw=audit_raw
    try:
        owner.psql("CREATE ROLE audit_guard_probe LOGIN PASSWORD '"+sentinel+"';",sensitive=True)
    except subprocess.CalledProcessError:
        pass
    else:
        raise AssertionError('audit hook guard failed')
    assert run([B/'psql','-XAtq','-d','account','-c',"SELECT count(*) FROM pg_roles WHERE rolname='audit_guard_probe';"],env=env).stdout.strip()=='0'
    # The global/default error policy must remain unchanged.
    assert run([B/'psql','-XAtq','-d','account','-c','SHOW log_min_error_statement;'],env=env).stdout.strip()=='error'
    run([B/'pg_ctl','-D',root/'data','stop','-m','fast'])
    server_log=(root/'server.log').read_bytes()
    assert control_sentinel.encode() in server_log
    assert sentinel.encode() not in server_log
    assert b'already exists' in server_log  # Confirm the intended SQL failure was logged.
    print(json.dumps({'default_error_statement_leak_control':'REPRODUCED','synthetic_role_conflict':'PASS','server_log_sentinel_absent':'PASS',
                      'management_session_panic':'PASS','detected_audit_hook_rejected_before_role_creation':'PASS','global_error_policy_unchanged':'PASS',
                      'runtime_dev':'NOT_RUN'}))
finally:
    subprocess.run([str(B/'pg_ctl'),'-D',str(root/'data'),'stop','-m','fast'],capture_output=True)
    shutil.rmtree(root)
