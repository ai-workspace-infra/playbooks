import base64,hashlib,json,os,pathlib,subprocess,sys,re
os.umask(0o077)
p=json.load(sys.stdin);root=pathlib.Path(p['root'])
assert re.fullmatch(r'prod-v[1-9][0-9]*',p['generation'])
assert root.parent.parent==pathlib.Path('/data/backups/web-saas')/p['generation']
assert re.fullmatch(r'full-snapshot-[0-9]{8}',root.parent.name)
assert re.fullmatch(r'[0-9]{8}T[0-9]{6}Z',root.name)
assert not (root/'SHA256SUMS').exists()
assert not (root/'vault/vault-raft.snapshot.enc').exists()
assert root.is_dir() and not root.is_symlink()
(root/'vault/vault-raft.snapshot.enc').write_bytes(base64.b64decode(p['vault']))
(root/'vault/vault-raft.inspect.txt').write_text(p['inspect'])
(root/'vault/vault-kv-paths-and-keys.json').write_text(json.dumps(p['keys'],indent=2)+'\n')
env=dict(os.environ,SNAPSHOT_KEY=p['key'])
names=subprocess.check_output(['docker','ps','--format','{{.Names}}']).decode().splitlines()
raw=subprocess.check_output(['docker','inspect']+names)
encrypted=subprocess.run(['openssl','enc','-aes-256-cbc','-salt','-pbkdf2','-iter','200000','-pass','env:SNAPSHOT_KEY'],input=raw,env=env,capture_output=True,check=True).stdout
(root/'runtime/container-inspect.json.enc').write_bytes(encrypted)
manifest=json.loads((root/'runtime/manifest.json').read_text())
for service in manifest['services']:
    live=json.loads(subprocess.check_output(['docker','inspect',service['name']]))[0]
    assert live['State']['StartedAt']==service['started_at'] and live['RestartCount']==service['restart_count'], 'runtime changed during capture'
manifest.update({'vault_status':'integrity_passed','vault_restore':'independent_instance_not_performed','configuration_archive':'encrypted etc/xcontrol and opt/doco-cd','scope':'logical account database, PostgreSQL globals, service configuration and TLS, encrypted container metadata, immutable image references, shared Vault Raft backup','excluded':'cloud disk images, old inactive databases, registry image layers, regenerable Docker caches/volumes','consistency':'DB one exported MVCC snapshot; runtime and Vault separately timestamped, not a global atomic snapshot'})
(root/'runtime/manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
hashes=[]
for f in sorted(root.rglob('*')):
    if f.is_file() and f.name!='SHA256SUMS':
        f.chmod(0o600);hashes.append(hashlib.sha256(f.read_bytes()).hexdigest()+'  '+str(f.relative_to(root)))
(root/'SHA256SUMS').write_text('\n'.join(hashes)+'\n');(root/'SHA256SUMS').chmod(0o600)
subprocess.run(['sha256sum','-c','SHA256SUMS'],cwd=root,check=True,capture_output=True)
os.sync()
print(json.dumps({'snapshot_dir':str(root),'sha256sums':hashes,'db_restore':'passed','vault_integrity':'passed','business_restarts':0,'keys':p['keys']}))
