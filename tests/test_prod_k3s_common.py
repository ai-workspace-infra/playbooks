import importlib.util
from pathlib import Path
import unittest
import yaml
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('node_guard',ROOT/'roles/vhosts/common/files/k3s_node_guard.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class CommonNodeTests(unittest.TestCase):
    def node(self,name='prod-k3s-v6'):return {'kind':'Node','metadata':{'name':name}}
    def pv(self,name='prod-k3s-v6'):
        return {'kind':'PersistentVolume','metadata':{'name':'fixture'},'spec':{'nodeAffinity':{'required':{'nodeSelectorTerms':[{'matchExpressions':[{'key':'kubernetes.io/hostname','operator':'In','values':[name]}]}]}}}}
    def test_existing_node_and_pv_match_are_required(self):
        self.assertEqual(1,m.verify([self.node(),self.pv()],'prod-k3s-v6')['persistent_volumes_checked'])
        with self.assertRaisesRegex(ValueError,'node identity'):m.verify([self.node('localhost')],'prod-k3s-v6')
        with self.assertRaisesRegex(ValueError,'PV affinity'):m.verify([self.node(),self.pv('old-node')],'prod-k3s-v6')
    def test_active_flux_is_rejected_before_any_hostname_change(self):
        active={'kind':'HelmRelease','metadata':{'name':'fixture'},'spec':{'suspend':False}}
        with self.assertRaisesRegex(ValueError,'Active Flux'):m.verify([self.node(),active],'prod-k3s-v6')
        active['spec']['suspend']=True
        self.assertEqual(1,m.verify([self.node(),active],'prod-k3s-v6')['paused_flux_checked'])
    def test_common_precedes_disk_and_cluster_roles(self):
        play=yaml.safe_load((ROOT/'prod_k3s_generation_bootstrap.yml').read_text())[0]
        tasks=play['pre_tasks']
        i=next(i for i,t in enumerate(tasks) if t.get('ansible.builtin.include_role',{}).get('name')=='vhosts/common')
        j=next(i for i,t in enumerate(tasks) if t.get('name')=='Install empty-disk and observation prerequisites')
        self.assertLess(i,j)
        self.assertEqual('node_bootstrap.yml',tasks[i]['ansible.builtin.include_role']['tasks_from'])
        self.assertEqual('web_saas_data_volume',play['roles'][0]['role'])
        self.assertTrue(all(role['when'] == "prod_k3s_bootstrap_phase == 'cluster'" for role in play['roles']))
        self.assertEqual("prod_k3s_bootstrap_phase == 'common'",play['post_tasks'][0]['when'])
    def test_guard_executes_before_hostname_and_does_not_rebind_volumes(self):
        tasks=yaml.safe_load((ROOT/'roles/vhosts/common/tasks/node_bootstrap.yml').read_text())
        guard=next(i for i,t in enumerate(tasks) if t.get('name')=='Common | verify existing node and persistent-volume affinity before hostname changes')
        hostname=next(i for i,t in enumerate(tasks) if 'ansible.builtin.hostname' in t)
        self.assertLess(guard,hostname)
        self.assertFalse(any('kubernetes.core.k8s' in t for t in tasks))
        self.assertIn('common_node_bootstrap_evidence',tasks[-1]['ansible.builtin.set_fact'])

if __name__=='__main__':unittest.main()
