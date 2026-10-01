"""OpenClaw must not run as a GCP OS Login deploy identity.

On an OS Login host the SSH user is the deploy principal's transient POSIX
login (sa_<id> / ext_<name>); `loginctl enable-linger` fails for it and its key
expires. The role runs OpenClaw as a local account there instead.
"""

import re
import unittest
from pathlib import Path

import yaml
from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / "roles" / "vhosts" / "gateway_openclaw"


def render(name, **facts):
    defaults = yaml.safe_load((ROLE / "defaults" / "main.yml").read_text(encoding="utf-8"))
    env = Environment()
    env.tests["match"] = lambda value, pattern: re.match(pattern, str(value)) is not None
    env.filters["default"] = lambda value, fallback="", boolean=False: (
        fallback if value is None or (boolean and not value) else value)
    context = {"ansible_os_family": "Debian", "ansible_env": {"USER": "root"}, **facts}
    values = {}
    for key in ("gateway_openclaw_app_user", "gateway_openclaw_oslogin_service_user",
                "gateway_openclaw_login_user", "gateway_openclaw_uses_oslogin_login",
                "gateway_openclaw_service_user"):
        if key in facts:
            values[key] = facts[key]
            continue
        raw = defaults[key]
        values[key] = env.from_string(raw).render(**context).strip() if isinstance(raw, str) else raw
        context[key] = values[key]
    return values[name]


class GatewayOpenClawOsLoginUserTest(unittest.TestCase):
    def test_os_login_hosts_run_openclaw_as_a_local_account(self):
        for login in ("sa_100360672584358897794", "ext_operator_example_com"):
            with self.subTest(login=login):
                self.assertEqual(render("gateway_openclaw_service_user", ansible_user=login), "openclaw")
                self.assertEqual(render("gateway_openclaw_uses_oslogin_login", ansible_user=login), "True")

    def test_other_hosts_keep_the_ssh_user_or_the_configured_account(self):
        self.assertEqual(render("gateway_openclaw_service_user", ansible_user="ubuntu"), "ubuntu")
        self.assertEqual(render("gateway_openclaw_service_user", ansible_user="root"), "root")
        self.assertEqual(render("gateway_openclaw_uses_oslogin_login", ansible_user="ubuntu"), "False")
        self.assertEqual(render("gateway_openclaw_service_user", ansible_user="sa_1",
                                gateway_openclaw_app_user="svc"), "svc")
        self.assertEqual(render("gateway_openclaw_uses_oslogin_login", ansible_user="sa_1",
                                gateway_openclaw_app_user="svc"), "False")

    def test_the_local_account_is_created_before_its_home_is_resolved(self):
        tasks = yaml.safe_load((ROLE / "tasks" / "main.yml").read_text(encoding="utf-8"))
        names = [task["name"] for task in tasks]
        create = tasks[names.index("Create the local OpenClaw service account for an OS Login host")]
        self.assertLess(names.index(create["name"]),
                        names.index("Resolve OpenClaw service account home from the target passwd database"))
        self.assertEqual(create["ansible.builtin.user"]["name"], "{{ gateway_openclaw_service_user }}")
        self.assertEqual(create["when"], "gateway_openclaw_uses_oslogin_login | bool")


if __name__ == "__main__":
    unittest.main()
