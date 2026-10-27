"""Validate exactly the reviewed platform ingress tree, never execute GitOps code."""
import hashlib,importlib.util,json,subprocess
from pathlib import Path
import yaml
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('dev_platform_scope',ROOT/'roles/platform/dev_platform_ingress/files/reconcile.py')
S=importlib.util.module_from_spec(spec);spec.loader.exec_module(S)

def digest(documents):
 rows=sorted(documents,key=lambda d:(d['kind'],d['metadata']['name']))
 return hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def validate_documents(documents):
 S.require(digest(documents)==S.MANIFEST,'platform content differs from the reviewed exact manifest')
 expected={('Namespace',S.NS),('ConfigMap','dev-ingress-values'),('OCIRepository','traefik-chart'),('HelmRelease','dev-ingress')}
 S.require({(d['kind'],d['metadata']['name']) for d in documents}==expected and len(documents)==4,'fixed four source resources required')
 for doc in documents:
  if doc['kind']!='Namespace':S.require(doc['metadata'].get('namespace')==S.NS,'platform namespace only')
 cm=next(d for d in documents if d['kind']=='ConfigMap');values=yaml.safe_load(cm['data']['values.yaml'])
 S.require(hashlib.sha256(cm['data']['values.yaml'].encode()).hexdigest()==S.VALUES,'platform values changed')
 S.require(values['rbac']=={'enabled':True,'namespaced':True} and values['providers']['kubernetesIngress']['namespaces']==S.WATCH,'only two namespace watchers permitted')
 return {'manifest_sha256':S.MANIFEST,'values_sha256':S.VALUES,'chart_digest':S.CHART,'image':S.IMAGE,'namespace':S.NS,'path':S.PATH}

RAW_FILES={'kustomization.yaml': '379fb8bdce40d438c060ab9f52fd4d2499c2a8316e7abd829f1392bb3e5df18c', 'namespace.yaml': 'b9778380a3556a77788f528ae2eabfdd94f1cb4595b1ee0b58f0ba6c6b1da79e', 'oci-repository.yaml': '0e1a72427a52f0a6b24d378b63af889e245cee80d301dd6db956e4ed521ae3e0', 'helmrelease.yaml': 'cc79e062a2b061de11f7a2b61e6fcb3dfc968c45769d8f8734877121f4cf811c', 'values.yaml': '7f664c6787d17bee1a07b4af395b8fcc4b2ac7cbfa341e0c59f5fc104dddb9e2'}

def validate_tree(root,sha):
 S.require(S.R.SHA.fullmatch(sha),'full reviewed platform source SHA required')
 root=Path(root).resolve()
 def run(args):
  p=subprocess.run(args,capture_output=True,text=True,timeout=60)
  S.require(p.returncode==0,'platform source validation failed; raw output suppressed');return p.stdout
 S.require(run(['git','-C',str(root),'rev-parse','HEAD']).strip()==sha,'platform checkout SHA mismatch')
 S.require(not run(['git','-C',str(root),'status','--porcelain','--',S.PATH[2:]]).strip(),'platform source tree is dirty')
 path=root/S.PATH[2:]
 S.require(all(not p.is_symlink() for p in [path,*path.parents] if p!=root.parent),'platform source parent must not be a symlink')
 S.require(path.is_dir() and all(not p.is_symlink() for p in path.rglob('*')),'platform source must not contain symlinks')
 S.require({p.name for p in path.iterdir() if p.name!='README.md'}==set(RAW_FILES),'only reviewed local source files allowed')
 for name,expected in RAW_FILES.items():
  S.require(hashlib.sha256((path/name).read_bytes()).hexdigest()==expected,'raw platform source changed before rendering')
 return validate_documents(list(yaml.safe_load_all(run(['kubectl','kustomize',str(path)]))))
