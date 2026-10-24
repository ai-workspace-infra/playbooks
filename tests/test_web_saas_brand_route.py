import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'roles/vhosts/web_saas_brand_route/files/reconcile.py'
spec = importlib.util.spec_from_file_location('brand_reconcile', SCRIPT)
owner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(owner)


class BrandRouteTest(unittest.TestCase):
    def test_preserves_existing_bytes_and_repeats_without_changes(self):
        original = '{\n\tadmin off\n}\nconsole.example.test {\n reverse_proxy console:3000\n}\n'
        result, base = owner.render(original, 'brand.example')
        self.assertEqual(base, original)
        self.assertTrue(result.startswith(original))
        self.assertIn('tls /etc/xcontrol/tls/brand.example/current/fullchain.pem', result)
        self.assertEqual(owner.render(result, 'brand.example'), (result, original))

    def test_refuses_existing_unmanaged_brand_site(self):
        with self.assertRaises(ValueError):
            owner.render('brand.example {\n}\n', 'brand.example')

    def test_refuses_ambiguous_markers(self):
        with self.assertRaises(ValueError):
            owner.render(owner.BEGIN + owner.BEGIN + owner.END, 'brand.example')


if __name__ == '__main__':
    unittest.main()
