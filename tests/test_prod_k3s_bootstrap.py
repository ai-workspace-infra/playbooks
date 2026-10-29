import copy
import importlib.util
from pathlib import Path
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

adapter = load('v6_adapter', 'scripts/prod_k3s_bootstrap.py')
verifier = load('v6_verify', 'roles/vhosts/k3s_platform_bootstrap/files/verify_paused_bootstrap.py')

class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.decl = {'global': {'environment':'prod','provider':'akamai-cloud','account':'manbuzhe2026','workspace':'prod-k3s-v6'},
                     'hosts':[{'name':'prod-k3s-v6','type':'g6-standard-2','image':'linode/debian13','region':'jp-tyo-3',
                               'groups':['prod','k3s'],'volume':{'label':'prod-k3s-v6-data','size':100},
                               'host_vars':{'target_generation':'prod-v6','k3s_platform_flux_bootstrap_mode':'declarative_paused'}}]}
        self.live = {'prod-k3s-v6':{'ip':'8.8.8.8','instance_id':101,'type':'g6-standard-2','image':'linode/debian13',
                                  'region':'jp-tyo-3','groups':['prod','k3s'],'host_vars':{'cloud_provider':'akamai-cloud','ansible_connection':'local'},
                                  'data_volume':{'linode_id':101,'volume_id':202,'size_gb':100,'region':'jp-tyo-3',
                                                 'label':'prod-k3s-v6-data','filesystem_path':'/dev/disk/by-id/scsi-volume'}}}
    def prepare(self):
        return adapter.prepare(self.live,self.decl,'a'*40,Path('/source'),Path('/receipt'))
    def test_validated_facts_cannot_override_connection(self):
        _, clean = self.prepare()
        facts = clean['hosts']['prod-k3s-v6']
        self.assertNotIn('ansible_connection', facts['host_vars'])
        self.assertEqual(202, facts['host_vars']['web_saas_data_expected_volume_id'])
    def test_reject_wrong_disk_owner(self):
        self.live['prod-k3s-v6']['data_volume']['linode_id']=999
        with self.assertRaisesRegex(ValueError,'another instance'): self.prepare()
    def test_reject_additional_host(self):
        self.live['old-prod']=copy.deepcopy(self.live['prod-k3s-v6'])
        with self.assertRaisesRegex(ValueError,'only the reviewed'): self.prepare()
    def test_reject_small_disk_and_wrong_region(self):
        for field,value in [('size_gb',99),('region','us-east')]:
            old=self.live['prod-k3s-v6']['data_volume'][field]
            self.live['prod-k3s-v6']['data_volume'][field]=value
            with self.assertRaises(ValueError): self.prepare()
            self.live['prod-k3s-v6']['data_volume'][field]=old
    def test_mount_dependency_precedes_install(self):
        tasks=yaml.safe_load((ROOT/'roles/vhosts/k3s_platform_bootstrap/tasks/main.yml').read_text())
        names=[t['name'] for t in tasks]
        self.assertLess(names.index('Bind K3s to the verified independent data mount'),names.index('Run k3s installer script'))

class AuditScopeTests(unittest.TestCase):
    def test_status_only_rules_and_real_identity_checks(self):
        tasks=yaml.safe_load((ROOT/'roles/vhosts/k3s_platform_bootstrap/tasks/audit_identity.yml').read_text())
        bundle=next(t['ansible.builtin.copy']['content'] for t in tasks if t.get('name')=='Declare status-only cluster audit access')
        role=list(yaml.safe_load_all(bundle))[1]
        self.assertEqual([{'apiGroups':[''],'resources':['nodes','namespaces'],'verbs':['get','list']}],role['rules'])
        script=(ROOT/'roles/vhosts/k3s_platform_bootstrap/files/verify_audit_identity.py').read_text()
        self.assertIn("'runuser', '-u', 'cluster-audit'",script)
        self.assertIn('dryRun=All',script)
        self.assertIn('!=403',script)

class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.obj={'kind':'GitRepository','metadata':{'name':'config-prod','namespace':'flux-system'},'spec':{'suspend':True,'ref':{'tag':'v1.0.0'}}}
        self.node={'expected_name':'prod-k3s-v6','metadata':{'name':'prod-k3s-v6'},'status':{'nodeInfo':{'kubeletVersion':'v1.37.1+k3s1','architecture':'amd64'},'conditions':[{'type':'Ready','status':'True'}]}}
    def test_observed_paused_spec(self):
        self.assertEqual({'GitRepository':1,'HelmRelease':0,'Kustomization':0},verifier.verify([self.obj],[self.obj],self.node,'v1.37.1+k3s1'))
    def test_active_or_wrong_pin_rejected(self):
        for patch in [{'suspend':False},{'ref':{'tag':'v2.0.0'}}]:
            actual=copy.deepcopy(self.obj); actual['spec'].update(patch)
            with self.assertRaises(ValueError): verifier.verify([self.obj],[actual],self.node,'v1.37.1+k3s1')
    def test_extra_root_rejected(self):
        extra=copy.deepcopy(self.obj);extra['metadata']['name']='platform-root'
        with self.assertRaises(ValueError): verifier.verify([self.obj],[self.obj,extra],self.node,'v1.37.1+k3s1')
    def test_not_ready_rejected(self):
        self.node['status']['conditions'][0]['status']='False'
        with self.assertRaises(ValueError): verifier.verify([self.obj],[self.obj],self.node,'v1.37.1+k3s1')

if __name__=='__main__': unittest.main()
