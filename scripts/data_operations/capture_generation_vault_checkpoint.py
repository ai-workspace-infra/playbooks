import hashlib,json,os,pathlib,re,subprocess,tempfile
os.umask(0o077)
import argparse
p=argparse.ArgumentParser();p.add_argument("--workdir",required=True);p.add_argument("--generation",required=True);a=p.parse_args()
assert re.fullmatch(r'prod-v[1-9][0-9]*',a.generation)
base=pathlib.Path(a.workdir).resolve();base.mkdir(mode=0o700,parents=True,exist_ok=False)
def run(args,data=None):
    p=subprocess.run(args,input=data,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    if p.returncode: raise RuntimeError('failed: '+' '.join(args[:4]))
    return p.stdout
key=run(['vault','kv','get','-field=web_saas_release_passphrase','kv/'+a.generation+'/backups']).decode().strip()
env=dict(os.environ,SNAPSHOT_KEY=key)
with tempfile.TemporaryDirectory(prefix='vault-snapshot-',dir=str(base)) as tmp:
    native=pathlib.Path(tmp)/'raft.snapshot'
    run(['vault','operator','raft','snapshot','save',str(native)])
    inspect=run(['vault','operator','raft','snapshot','inspect',str(native)])
    encrypted=base/'vault-raft.snapshot.enc'
    subprocess.run(['openssl','enc','-aes-256-cbc','-salt','-pbkdf2','-iter','200000','-pass','env:SNAPSHOT_KEY','-in',str(native),'-out',str(encrypted)],env=env,check=True,capture_output=True)
    check=pathlib.Path(tmp)/'roundtrip.snapshot'
    subprocess.run(['openssl','enc','-d','-aes-256-cbc','-pbkdf2','-iter','200000','-pass','env:SNAPSHOT_KEY','-in',str(encrypted),'-out',str(check)],env=env,check=True,capture_output=True)
    assert hashlib.sha256(native.read_bytes()).digest()==hashlib.sha256(check.read_bytes()).digest()
    run(['vault','operator','raft','snapshot','inspect',str(check)])
    (base/'vault-raft.inspect.txt').write_bytes(inspect)
print(json.dumps({'vault_archive_sha256':hashlib.sha256(encrypted.read_bytes()).hexdigest(),'vault_integrity':'passed','independent_vault_restore':'not_performed'}))
