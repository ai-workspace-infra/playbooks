import unittest
from pathlib import Path
import yaml
import jinja2

ROOT = Path(__file__).resolve().parents[1]
K3S_ROLE = ROOT / 'roles' / 'vhosts' / 'k3s-cluster-server'
FLUX_ROLE = ROOT / 'roles' / 'vhosts' / 'flux_controllers'


class K3sPlatformMinimalBaselineTest(unittest.TestCase):
    def setUp(self):
        self.k3s_defaults = yaml.safe_load((K3S_ROLE / 'defaults' / 'main.yml').read_text())
        self.flux_defaults = yaml.safe_load((FLUX_ROLE / 'defaults' / 'main.yml').read_text())
        self.template_text = (K3S_ROLE / 'templates' / 'k3s-config.yaml.j2').read_text()
        self.jinja_env = jinja2.Environment()
        self.jinja_env.filters['bool'] = lambda v: str(v).lower() in ('true', 'yes', '1')

    def test_k3s_defaults_support_datastore_choice_and_memory_tuning(self):
        self.assertIn('k3s_platform_datastore', self.k3s_defaults)
        self.assertIn('k3s_platform_cluster_init', self.k3s_defaults)
        self.assertEqual(self.k3s_defaults['k3s_platform_datastore'], 'sqlite')
        self.assertFalse(self.k3s_defaults['k3s_platform_cluster_init'])
        self.assertTrue(self.k3s_defaults['k3s_platform_enable_mem_tuning'])
        self.assertEqual(self.k3s_defaults['k3s_platform_gomemlimit'], '768MiB')
        self.assertEqual(self.k3s_defaults['k3s_platform_gogc'], '50')

    def test_k3s_config_template_etcd_vs_sqlite(self):
        template = self.jinja_env.from_string(self.template_text)

        # 1. Default etcd mode renders cluster-init: true
        context_etcd = dict(self.k3s_defaults)
        context_etcd.update({
            'k3s_platform_write_kubeconfig_mode': '0600',
            'k3s_platform_node_name': 'test-node',
            'k3s_platform_cluster_cidr': '10.42.0.0/16',
            'k3s_platform_service_cidr': '10.43.0.0/16',
            'k3s_platform_cluster_dns': '10.43.0.10',
            'k3s_platform_install_dir': '/opt/rancher/k3s',
            'k3s_platform_local_storage_path': '',
            'k3s_platform_disable_components': ['traefik'],
            'k3s_platform_kube_apiserver_args': [],
            'k3s_platform_tls_sans': [],
            'k3s_platform_datastore': 'etcd',
            'k3s_platform_cluster_init': True,
        })
        rendered_etcd = template.render(context_etcd)
        self.assertIn('cluster-init: true', rendered_etcd)

        # 2. SQLite minimal profile omits cluster-init: true
        context_sqlite = dict(context_etcd)
        context_sqlite['k3s_platform_datastore'] = 'sqlite'
        context_sqlite['k3s_platform_cluster_init'] = False
        rendered_sqlite = template.render(context_sqlite)
        self.assertNotIn('cluster-init: true', rendered_sqlite)

    def test_flux_controllers_minimal_components_and_concurrency(self):
        self.assertIn('k3s_platform_flux_components', self.flux_defaults)
        components = self.flux_defaults['k3s_platform_flux_components']
        self.assertEqual(components, ['source-controller', 'kustomize-controller', 'helm-controller'])
        self.assertNotIn('notification-controller', components)

        self.assertIn('k3s_platform_flux_concurrency', self.flux_defaults)
        concurrency = self.flux_defaults['k3s_platform_flux_concurrency']
        self.assertEqual(concurrency.get('kustomize-controller'), 2)
        self.assertEqual(concurrency.get('helm-controller'), 2)


if __name__ == '__main__':
    unittest.main()
