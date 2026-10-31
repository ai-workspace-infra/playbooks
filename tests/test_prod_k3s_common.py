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
    def test_common_requires_three_os_identities_for_receipt(self):
        tasks=yaml.safe_load((ROOT/'roles/vhosts/common/tasks/node_bootstrap.yml').read_text())
        profile=next(i for i,t in enumerate(tasks) if t.get('ansible.builtin.include_tasks')=='os_identity.yml')
        receipt=next(i for i,t in enumerate(tasks) if 'common_node_bootstrap_evidence' in t.get('ansible.builtin.set_fact',{}))
        self.assertLess(profile,receipt)
        self.assertEqual('OS_IDENTITY_CHECKS_PASSED',tasks[receipt]['ansible.builtin.set_fact']['common_node_bootstrap_evidence']['os_identities'])
        self.assertFalse((ROOT/'roles/vhosts/common/tasks/audit_user.yml').exists())
        self.assertFalse((ROOT/'roles/vhosts/k3s_platform_bootstrap/tasks/audit_identity.yml').exists())

    def test_main_bootstrap_requires_volume_before_common_receipt(self):
        play=yaml.safe_load((ROOT/'k3s_platform_bootstrap.yml').read_text())[0]
        common=next(t for t in play['pre_tasks'] if t.get('ansible.builtin.include_role',{}).get('name')=='vhosts/common')
        self.assertTrue(common['vars']['common_node_require_data_volume'])
        self.assertFalse(any(r['role']=='web_saas_data_volume' for r in play['roles']))
        tasks=yaml.safe_load((ROOT/'roles/vhosts/common/tasks/node_bootstrap.yml').read_text())
        volume=next(i for i,t in enumerate(tasks) if t.get('ansible.builtin.include_role',{}).get('name')=='web_saas_data_volume')
        check=next(i for i,t in enumerate(tasks) if t.get('name')=='Common | require independent data volume readiness')
        receipt=next(i for i,t in enumerate(tasks) if 'common_node_bootstrap_evidence' in t.get('ansible.builtin.set_fact',{}))
        self.assertLess(volume,check)
        self.assertLess(check,receipt)
        self.assertIn('web_saas_data_evidence.mounted | default(false) | bool',tasks[check]['ansible.builtin.assert']['that'])

    def test_volume_cloud_and_filesystem_labels_are_distinct(self):
        play=yaml.safe_load((ROOT/'k3s_platform_bootstrap.yml').read_text())[0]
        identity=next(t for t in play['pre_tasks'] if t.get('name')=='Require one matching node and matching cloud scope')
        rules=identity['ansible.builtin.assert']['that']
        self.assertIn('data_volume.label == bootstrap_node_candidates[0].volume.label', rules)
        self.assertTrue(any('volume.filesystem_label' in rule and '{1,16}' in rule for rule in rules))
        bind=next(t for t in play['pre_tasks'] if t.get('name')=='Bind runtime sources and data device without generating cloud facts')
        self.assertEqual(bind['ansible.builtin.set_fact']['web_saas_data_filesystem_label'], '{{ bootstrap_node_candidates[0].volume.filesystem_label }}')

    def test_guard_executes_before_hostname_and_does_not_rebind_volumes(self):
        tasks=yaml.safe_load((ROOT/'roles/vhosts/common/tasks/node_bootstrap.yml').read_text())
        guard=next(i for i,t in enumerate(tasks) if t.get('name')=='Common | verify existing node and persistent-volume affinity before hostname changes')
        hostname=next(i for i,t in enumerate(tasks) if 'ansible.builtin.hostname' in t)
        self.assertLess(guard,hostname)
        self.assertFalse(any('kubernetes.core.k8s' in t for t in tasks))
        self.assertIn('common_node_bootstrap_evidence',tasks[-1]['ansible.builtin.set_fact'])

if __name__=='__main__':unittest.main()
