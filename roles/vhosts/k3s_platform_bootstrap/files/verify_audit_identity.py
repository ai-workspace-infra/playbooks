#!/usr/bin/env python3
"""Bounded live allow/deny tests; credentials remain process-local."""
import base64
import json
import os
import ssl
import subprocess
import urllib.error
import urllib.request
import yaml


def main():
    def run(*args):
        return subprocess.run(args, capture_output=True, text=True, check=False)
    if run('runuser', '-u', 'cluster-audit', '--', 'systemctl', 'is-active', 'k3s').returncode != 0:
        raise ValueError('Audit user cannot read service status')
    for flag, path in [('-r','/etc/rancher/k3s/k3s.yaml'),('-w','/etc'),('-w','/data/k3s/storage')]:
        if run('runuser','-u','cluster-audit','--','test',flag,path).returncode != 1:
            raise ValueError('System audit permission boundary failed')
    groups=run('id','-nG','cluster-audit')
    if groups.returncode or groups.stdout.split()!=['cluster-audit']:
        raise ValueError('Audit identity has unexpected supplementary groups')
    config=yaml.safe_load(open(os.environ['KUBECONFIG']))['clusters'][0]['cluster']
    context=ssl.create_default_context(cadata=base64.b64decode(config['certificate-authority-data']).decode())
    result=run('kubectl','-n','flux-system','create','token','cluster-audit','--duration=10m')
    if result.returncode:
        raise ValueError('Could not issue bounded audit test identity')
    token=result.stdout.strip()
    def status(path, body=None):
        req=urllib.request.Request(config['server']+path, data=json.dumps(body).encode() if body else None,
            headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(req,context=context,timeout=20) as response:
                return response.status
        except urllib.error.HTTPError as err:
            return err.code
    if status('/api/v1/nodes')!=200 or status('/api/v1/namespaces')!=200:
        raise ValueError('Audit status read was not allowed')
    probes=[('/api/v1/namespaces/flux-system/secrets',None),
            ('/api/v1/nodes/prod-k3s-v6/proxy',None),
            ('/api/v1/namespaces?dryRun=All',{'apiVersion':'v1','kind':'Namespace','metadata':{'name':'audit-denial-probe'}})]
    if any(status(path,body)!=403 for path,body in probes):
        raise ValueError('Audit sensitive read or write was not denied')
    print(json.dumps({'result':'AUDIT_ALLOW_DENY_VERIFIED','system_identity':'cluster-audit',
                      'cluster_identity':'system:serviceaccount:flux-system:cluster-audit',
                      'allowed':['system-status','nodes','namespaces'],
                      'denied':['admin-kubeconfig','system-write','data-write','secrets','node-proxy','namespace-write-dry-run']}))


if __name__=='__main__':main()
