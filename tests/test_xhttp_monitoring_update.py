import re
import tomllib
import unittest
from pathlib import Path

import yaml
from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[1]


class XhttpMonitoringUpdateTests(unittest.TestCase):
    def test_indented_and_flat_existing_sinks_keep_all_inputs(self):
        tasks = yaml.safe_load((ROOT / 'roles/vhosts/vector-agent/tasks/xhttp_monitoring.yml').read_text())
        task = next(t for t in tasks if t['name'] == 'Connect XHTTP metrics to existing remote-write sink')
        env = Environment()
        env.filters['regex_replace'] = lambda value, pattern, replacement: re.sub(pattern, replacement, value)
        template = env.from_string(task['ansible.builtin.set_fact']['xhttp_vector_candidate'])
        for indent in ['', ' ' * 22]:
            with self.subTest(indent=len(indent)):
                base = '[sinks.prometheus_remote]\ntype = "prometheus_remote_write"\n' + indent + 'inputs = ["existing_metrics"]\nendpoint = "https://example.test/write"\n[sinks.billing]\ntype = "http"\ninputs = ["snapshot"]\n'
                rendered = template.render(xhttp_vector_base=base)
                config = tomllib.loads(rendered)
                self.assertEqual(config['sinks']['prometheus_remote']['inputs'],
                                 ['xhttp_caddy_labels', 'xhttp_event_metrics', 'existing_metrics'])
                self.assertEqual(config['sinks']['billing']['inputs'], ['snapshot'])
                self.assertEqual(template.render(xhttp_vector_base=rendered), rendered)
