"""The OpenClaw gateway must not run host commands without approval.

OpenClaw's built-in host default is security=full, ask=off, askFallback=full.
The role pins allowlist / on-miss / deny in both openclaw.json tools.exec and
the host approvals file, because askFallback is only read from the host file.
"""

import json
import unittest
from pathlib import Path

import yaml
from jinja2 import Environment, Undefined

ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / "roles" / "vhosts" / "gateway_openclaw"


def load_tasks():
    tasks = yaml.safe_load((ROLE / "tasks" / "main.yml").read_text(encoding="utf-8"))
    return {task["name"]: task for task in tasks}


class _Placeholder(Undefined):
    def __str__(self):
        return "x"


class GatewayOpenClawExecApprovalsTest(unittest.TestCase):
    def test_defaults_require_approval_and_deny_on_fallback(self):
        defaults = yaml.safe_load((ROLE / "defaults" / "main.yml").read_text(encoding="utf-8"))
        self.assertEqual(defaults["gateway_openclaw_exec_security"], "allowlist")
        self.assertEqual(defaults["gateway_openclaw_exec_ask"], "on-miss")
        self.assertEqual(defaults["gateway_openclaw_exec_ask_fallback"], "deny")

    def test_config_template_requests_the_same_exec_policy(self):
        env = Environment(undefined=_Placeholder)
        env.filters["to_json"] = lambda value: json.dumps("x" if isinstance(value, Undefined) else value)
        env.filters["int"] = lambda value: 1
        env.filters["bool"] = lambda value: False
        env.filters["unique"] = lambda value: value
        env.filters["list"] = lambda value: []
        template = env.from_string((ROLE / "templates" / "openclaw.json.j2").read_text(encoding="utf-8"))
        rendered = json.loads(template.render(
            gateway_openclaw_exec_security="allowlist",
            gateway_openclaw_exec_ask="on-miss",
        ))
        self.assertEqual(rendered["tools"]["exec"], {"security": "allowlist", "ask": "on-miss"})

    def test_host_file_keeps_socket_and_allowlist_and_only_merges_defaults(self):
        tasks = load_tasks()
        enforce = tasks["Enforce OpenClaw host exec approval defaults"]
        content = enforce["ansible.builtin.copy"]["content"]
        self.assertIn("gateway_openclaw_exec_approvals_current", content)
        self.assertIn("combine(gateway_openclaw_exec_approvals_defaults)", content)
        self.assertEqual(enforce["ansible.builtin.copy"]["mode"], "0600")
        self.assertTrue(enforce["no_log"])
        self.assertEqual(enforce["notify"], "Restart openclaw")
        resolve = tasks["Resolve OpenClaw host exec approval defaults"]
        self.assertEqual(resolve["ansible.builtin.set_fact"]["gateway_openclaw_exec_approvals_defaults"], {
            "security": "{{ gateway_openclaw_exec_security }}",
            "ask": "{{ gateway_openclaw_exec_ask }}",
            "askFallback": "{{ gateway_openclaw_exec_ask_fallback }}",
        })


if __name__ == "__main__":
    unittest.main()
