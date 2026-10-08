"""The OpenClaw gateway must not run host commands without approval.

OpenClaw's built-in host default is security=full, ask=off, askFallback=full.
The role requests tools.exec mode "ask" in openclaw.json and sets the host
approval defaults (kept in OpenClaw's SQLite state since 2026.9) to
allowlist / on-miss / deny with `openclaw exec-policy set`, because
askFallback only comes from the host defaults.
"""

import json
import unittest
from pathlib import Path

import yaml
from jinja2 import Environment, Undefined

ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / "roles" / "vhosts" / "gateway_openclaw"


def load_tasks():
    return yaml.safe_load((ROLE / "tasks" / "main.yml").read_text(encoding="utf-8"))


class _Placeholder(Undefined):
    def __str__(self):
        return "x"

    def __iter__(self):
        return iter(())


class GatewayOpenClawExecApprovalsTest(unittest.TestCase):
    def test_defaults_require_approval_and_deny_on_fallback(self):
        defaults = yaml.safe_load((ROLE / "defaults" / "main.yml").read_text(encoding="utf-8"))
        self.assertEqual(defaults["gateway_openclaw_exec_mode"], "ask")
        self.assertEqual(defaults["gateway_openclaw_exec_security"], "allowlist")
        self.assertEqual(defaults["gateway_openclaw_exec_ask"], "on-miss")
        self.assertEqual(defaults["gateway_openclaw_exec_ask_fallback"], "deny")

    def test_config_template_requests_ask_mode(self):
        env = Environment(undefined=_Placeholder)
        env.filters["to_json"] = lambda value: json.dumps("x" if isinstance(value, Undefined) else value)
        env.filters["int"] = lambda value: 1
        env.filters["bool"] = lambda value: False
        env.filters["unique"] = lambda value: value
        env.filters["list"] = lambda value: []
        env.filters["dict2items"] = lambda value: []
        env.filters["map"] = lambda value, **kwargs: []
        template = env.from_string((ROLE / "templates" / "openclaw.json.j2").read_text(encoding="utf-8"))
        rendered = json.loads(template.render(gateway_openclaw_exec_mode="ask"))
        self.assertEqual(rendered["tools"]["exec"], {"mode": "ask"})

    def test_host_defaults_are_set_through_the_cli_after_the_upgrade_migration(self):
        tasks = load_tasks()
        names = [task["name"] for task in tasks]
        enforce = tasks[names.index("Enforce OpenClaw exec approval defaults")]
        cmd = enforce["ansible.builtin.command"]["cmd"]
        self.assertIn("exec-policy set", cmd)
        for var in ("gateway_openclaw_exec_security", "gateway_openclaw_exec_ask", "gateway_openclaw_exec_ask_fallback"):
            self.assertIn(var, cmd)
        self.assertLess(names.index("Migrate OpenClaw state after a version upgrade"),
                        names.index("Inspect OpenClaw exec approval defaults"))
        self.assertLess(names.index("Enforce OpenClaw exec approval defaults"),
                        names.index("Restore immutable flag on OpenClaw gateway JSON config"))
        self.assertFalse(any("exec-approvals.json" in json.dumps(task) and "copy" in json.dumps(task) for task in tasks))


if __name__ == "__main__":
    unittest.main()
