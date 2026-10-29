#!/usr/bin/env python3
"""Read existing cluster/PV identity before any common hostname change."""
import argparse
import json
from pathlib import Path
import subprocess


def verify(items, expected):
    nodes=[x['metadata']['name'] for x in items if x['kind']=='Node']
    if nodes!=[expected]:
        raise ValueError('Existing K3s node identity differs; do not rename a live stateful node')
    pvs=[x for x in items if x['kind']=='PersistentVolume']
    for pv in pvs:
        terms=pv.get('spec',{}).get('nodeAffinity',{}).get('required',{}).get('nodeSelectorTerms',[])
        for term in terms:
            for expr in term.get('matchExpressions',[])+term.get('matchFields',[]):
                if expr.get('key') in {'kubernetes.io/hostname','metadata.name'}:
                    if expr.get('operator')!='In' or expr.get('values')!=[expected]:
                        raise ValueError('PV affinity differs from canonical node; explicit recovery review required')
    flux=[x for x in items if x['kind'] in {'GitRepository','Kustomization','HelmRelease'}]
    if any(x.get('spec',{}).get('suspend') is not True for x in flux):
        raise ValueError('Active Flux graph cannot be restaged by fresh node bootstrap')
    return {'existing_cluster':True,'node':expected,'persistent_volumes_checked':len(pvs),'paused_flux_checked':len(flux)}


def main():
    p=argparse.ArgumentParser();p.add_argument('--node',required=True);a=p.parse_args()
    binaries=[x for x in ('/usr/local/bin/k3s','/usr/bin/k3s') if Path(x).is_file()]
    if not binaries and not Path('/etc/rancher/k3s/k3s.yaml').exists():
        print(json.dumps({'existing_cluster':False,'persistent_volumes_checked':0}));return
    if not binaries:raise ValueError('K3s config exists without a usable control binary')
    result=subprocess.run([binaries[0],'kubectl','get','nodes,persistentvolumes','-o','json','--request-timeout=30s'],
        capture_output=True,text=True,env={'PATH':'/usr/local/bin:/usr/bin:/bin','KUBECONFIG':'/etc/rancher/k3s/k3s.yaml'})
    if result.returncode:raise ValueError('Existing K3s state cannot be verified before hostname changes')
    items=json.loads(result.stdout)['items']
    env={'PATH':'/usr/local/bin:/usr/bin:/bin','KUBECONFIG':'/etc/rancher/k3s/k3s.yaml'}
    available=subprocess.run([binaries[0],'kubectl','api-resources','-o','name','--request-timeout=30s'],capture_output=True,text=True,env=env)
    if available.returncode:raise ValueError('Could not discover existing control resources')
    known={'gitrepositories.source.toolkit.fluxcd.io','kustomizations.kustomize.toolkit.fluxcd.io','helmreleases.helm.toolkit.fluxcd.io'}
    selected=sorted(known.intersection(available.stdout.splitlines()))
    if selected:
        observed=subprocess.run([binaries[0],'kubectl','get',','.join(selected),'-A','-o','json','--request-timeout=30s'],capture_output=True,text=True,env=env)
        if observed.returncode:raise ValueError('Could not verify existing Flux state')
        items+=json.loads(observed.stdout)['items']
    print(json.dumps(verify(items,a.node)))


if __name__=='__main__':main()
