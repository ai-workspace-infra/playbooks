"""Bounded read-only OCI metadata for the existing Dev registry owner.
No Vault/session lookup, credential file, raw config return, writes or main().
Caller must validate the fixed candidate image allowlist and suppress raw errors.
"""
import base64,hashlib,json,re,urllib.parse,urllib.request
LIMIT=2_000_000

class SafeRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,req,fp,code,msg,headers,newurl):
  p=urllib.parse.urlsplit(newurl)
  if p.scheme!='https' or p.hostname not in ('ghcr.io','pkg-containers.githubusercontent.com') or p.username or p.password or p.port not in (None,443):
   raise ValueError('unapproved registry redirect')
  request=super().redirect_request(req,fp,code,msg,headers,newurl)
  if request is not None and p.hostname!='ghcr.io':request.remove_header('Authorization');request.remove_header('authorization')
  return request

def inspect(reference,username,token,expected_repository,expected_revision):
 try:
  match=re.fullmatch(r'ghcr.io/([a-z0-9_./-]+)@(sha256:[a-f0-9]{64})',reference)
  if not match:raise ValueError('immutable GHCR reference required')
  repository,digest=match.groups()
  opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),SafeRedirect())
  def read(path,authorization,expected=None):
   headers={'Authorization':authorization,'Accept':'application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.docker.distribution.manifest.v2+json'}
   with opener.open(urllib.request.Request('https://ghcr.io/'+path,headers=headers),timeout=30) as response:
    raw=response.read(LIMIT+1);reported=response.headers.get('Docker-Content-Digest')
    if len(raw)>LIMIT:raise ValueError('oversized metadata')
   if expected and ('sha256:'+hashlib.sha256(raw).hexdigest()!=expected or (reported and reported!=expected)):raise ValueError('digest mismatch')
   return json.loads(raw)
  basic='Basic '+base64.b64encode((username+':'+token).encode()).decode()
  bearer='Bearer '+read('token?'+urllib.parse.urlencode({'service':'ghcr.io','scope':'repository:'+repository+':pull'}),basic)['token']
  root=read('v2/'+repository+'/manifests/'+digest,bearer,digest)
  if 'manifests' in root:
   selected=[row for row in root['manifests'] if row.get('platform',{}).get('os')=='linux' and row.get('platform',{}).get('architecture')=='amd64']
   if len(selected)!=1:raise ValueError('unique amd64 platform required')
   platform=selected[0]['digest']
   if not re.fullmatch(r'sha256:[a-f0-9]{64}',platform):raise ValueError('invalid platform digest')
   manifest=read('v2/'+repository+'/manifests/'+platform,bearer,platform)
  else:platform=digest;manifest=root
  config_digest=manifest['config']['digest']
  if not re.fullmatch(r'sha256:[a-f0-9]{64}',config_digest):raise ValueError('invalid config digest')
  config=read('v2/'+repository+'/blobs/'+config_digest,bearer,config_digest)
  if config.get('os')!='linux' or config.get('architecture')!='amd64':raise ValueError('platform mismatch')
  labels=config.get('config',{}).get('Labels') or {}
  source=labels.get('org.opencontainers.image.source');revision=labels.get('org.opencontainers.image.revision')
  return {'image':reference,'manifest_digest':digest,'platform_manifest_digest':platform,'config_digest':config_digest,'platform':'linux/amd64',
          'source_label_match':('UNKNOWN' if source is None else isinstance(source,str) and source.rstrip('/').removesuffix('.git')=='https://github.com/'+expected_repository),
          'revision_label_match':('UNKNOWN' if revision is None else revision==expected_revision),
          'actual_revision':revision if isinstance(revision,str) and re.fullmatch(r'[a-f0-9]{40}',revision) else 'UNKNOWN'}
 except Exception:raise ValueError('bounded immutable OCI metadata verification unavailable or mismatched') from None
