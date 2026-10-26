#!/usr/bin/env python3
"""Fixed Dev registry projection; secret material exists only in process memory/stdin."""
import base64,json,os,subprocess,urllib.request

def build(namespace,record):
 if namespace not in ('feature-xconnect-referral-sit','web-saas-uat'):raise ValueError('Dev namespace required')
 username=record.get('GHCR_USERNAME');token=record.get('GHCR_TOKEN')
 if not isinstance(username,str) or not username or not isinstance(token,str) or not token:raise ValueError('Dev registry fields missing')
 auth=base64.b64encode((username+':'+token).encode()).decode()
 config=json.dumps({'auths':{'ghcr.io':{'username':username,'password':token,'auth':auth}}})
 return {'apiVersion':'v1','kind':'Secret','metadata':{'namespace':namespace,'name':'web-saas-registry','labels':{'app.kubernetes.io/managed-by':'dev-runtime-secret-sync'}},'type':'kubernetes.io/dockerconfigjson','data':{'.dockerconfigjson':base64.b64encode(config.encode()).decode()}}

def main():
 ns=os.environ['DEV_REGISTRY_NAMESPACE'];host=os.environ['DEV_REGISTRY_HOST']
 import ipaddress
 ipaddress.ip_address(host)
 req=urllib.request.Request('https://vault.svc.plus/v1/kv/data/CICD/dev/ghcr',headers={'X-Vault-Token':os.environ['DEV_SYNC_VAULT_TOKEN']})
 with urllib.request.urlopen(req,timeout=20) as response:record=json.load(response)['data']
 secret=build(ns,record['data'])
 args=['ssh','-F','/dev/null','-o','StrictHostKeyChecking=yes','-o','GlobalKnownHostsFile=/dev/null','-o','UpdateHostKeys=no','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','UserKnownHostsFile='+os.environ['DEV_REGISTRY_KNOWN_HOSTS'],'-i',os.environ['DEV_REGISTRY_PUBLIC_IDENTITY'],'root@'+host,'/usr/local/bin/k3s kubectl apply --server-side --field-manager=dev-runtime-secret-sync -f - -o name']
 p=subprocess.run(args,input=json.dumps(secret),text=True,capture_output=True,timeout=60)
 if p.returncode or p.stdout.strip()!='secret/web-saas-registry':raise RuntimeError('registry projection failed')
 print(json.dumps({'result':'configured','namespace':ns,'secret_name':'web-saas-registry','source_path':'kv/CICD/dev/ghcr','source_version':record['metadata']['version']}))
if __name__=='__main__':
 try:main()
 except Exception:raise SystemExit('Dev registry projection failed; sensitive diagnostics suppressed') from None
