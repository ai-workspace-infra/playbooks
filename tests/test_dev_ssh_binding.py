import copy,os,sys,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import dev_ssh_binding as S

class SharedBinding(unittest.TestCase):
 def session(self,policies=None):return {'client_token':'synthetic-only','token_type':'batch','lease_duration':1200,'policies':policies or ['dev-bootstrap']}
 def secret(self):return {'INSTANCE_ID':'123','SSH_USER':'root','SSH_PRIVATE_KEY':'synthetic-private-stdin-only','SSH_PUBLIC_KEY':'synthetic-public','SSH_KNOWN_HOSTS':'synthetic-known-hosts'}
 def request(self,session,secret):
  def response(url,*args):
   if 'audience=vault' in url:return {'value':'synthetic-oidc'}
   if url.endswith('/login'):return {'auth':session}
   if '/v1/kv/' in url:return {'data':{'data':secret}}
   raise AssertionError('unapproved auth endpoint')
  return response
 def test_existing_binding_preserves_strict_transport_and_no_private_key_file(self):
  cmdb={'dev-k3s-v5':{'ip':'93.184.216.34','host_vars':{}}};original=copy.deepcopy(cmdb);calls=[]
  def run(args,env=None,stdin=None,timeout=1200):
   calls.append((args,stdin))
   return 'SSH_AUTH_SOCK=/tmp/synthetic-agent; SSH_AGENT_PID=123;' if args==['ssh-agent','-s'] else ''
  with patch.dict(os.environ,{'ACTIONS_ID_TOKEN_REQUEST_URL':'https://token.invalid?x=1','ACTIONS_ID_TOKEN_REQUEST_TOKEN':'synthetic'}),patch.object(S,'request',side_effect=self.request(self.session(),self.secret())),patch.object(S,'run',side_effect=run),patch.object(S.subprocess,'run'):
   with S.binding(cmdb,'123',tagged=True) as value:
    self.assertEqual(value.env['ANSIBLE_HOST_KEY_CHECKING'],'True')
    self.assertIn('StrictHostKeyChecking=yes',value.env['ANSIBLE_SSH_ARGS']);self.assertIn('IdentitiesOnly=yes',value.env['ANSIBLE_SSH_ARGS'])
    for file in value.directory.iterdir():self.assertNotIn('synthetic-private',file.read_text())
   self.assertEqual(cmdb,original)
   self.assertTrue(any(args==['ssh-add','-'] and stdin=='synthetic-private-stdin-only\n' for args,stdin in calls))
 def test_root_or_wrong_instance_rejected_before_ssh(self):
  for session,secret in [(self.session(['root']),self.secret()),(self.session(),dict(self.secret(),INSTANCE_ID='different'))]:
   with patch.dict(os.environ,{'ACTIONS_ID_TOKEN_REQUEST_URL':'https://token.invalid?x=1','ACTIONS_ID_TOKEN_REQUEST_TOKEN':'synthetic'}),patch.object(S,'request',side_effect=self.request(session,secret)),patch.object(S,'run',side_effect=AssertionError('no SSH')):
    with self.assertRaises(ValueError):
     with S.binding({'dev-k3s-v5':{'ip':'93.184.216.34','host_vars':{}}},'123',tagged=True):self.fail('must block')
if __name__=='__main__':unittest.main()
