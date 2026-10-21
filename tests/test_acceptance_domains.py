import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("domains", Path(__file__).parents[1] / "scripts/render-acceptance-domains.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class AcceptanceDomains(unittest.TestCase):
    def test_isolated_services(self):
        result = module.domains('sit', 'v4-acceptance-20261021', 'example.com')
        self.assertEqual(result['postgresql'], 'postgresql-sit-v4-acceptance-20261021.example.com')
        self.assertEqual(len(set(result.values())), 4)

    def test_environment_override(self):
        self.assertEqual(module.domains('uat', 'run1', 'example.com')['console'], 'console-uat-run1.example.com')

    def test_reject_production_or_invalid_names(self):
        for args in [('prod', 'run1', 'example.com'), ('sit', '', 'example.com'),
                     ('sit', 'x' * 63, 'example.com'), ('sit', 'run1', '*.example.com')]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                module.domains(*args)


if __name__ == '__main__':
    unittest.main()
