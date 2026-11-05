"""Validation and integrity tests for Guance Cloud standard dashboard definitions."""
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
DASHBOARDS_DIR = ROOT / "dashboards" / "guance-cloud"
ROLES_DIR = ROOT / "roles" / "guance-dashboard"

FILES = [
    ("caddy-dashboard.json", 25, ["environment", "cluster", "service", "instance", "interval"]),
    ("postgres-unified-overview.json", 14, ["environment", "cluster", "service", "instance", "datname"]),
    ("starsl-k8s-dashboard.json", 33, ["cluster", "environment", "Node", "NameSpace", "Container", "Pod"]),
]


class TestGuanceCloudDashboards(unittest.TestCase):
    def test_all_files_exist_in_both_locations(self):
        for fname, _, _ in FILES:
            dash_path = DASHBOARDS_DIR / fname
            role_path = ROLES_DIR / fname
            self.assertTrue(dash_path.exists(), f"Missing {dash_path}")
            self.assertTrue(role_path.exists(), f"Missing {role_path}")
            self.assertEqual(dash_path.read_bytes(), role_path.read_bytes(), f"Mismatch between {fname} locations")

    def test_json_validity_and_panel_counts(self):
        for fname, expected_panels, expected_vars in FILES:
            path = DASHBOARDS_DIR / fname
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            charts = data.get("main", {}).get("charts", [])
            self.assertEqual(len(charts), expected_panels, f"Panel count mismatch in {fname}")
            
            vars_list = data.get("main", {}).get("vars", [])
            var_codes = [v.get("code") for v in vars_list]
            self.assertEqual(var_codes, expected_vars, f"Variable codes mismatch in {fname}")

    def test_no_grafana_macros_or_unresolved_dollars(self):
        for fname, _, _ in FILES:
            raw = (DASHBOARDS_DIR / fname).read_text(encoding="utf-8")
            macros = re.findall(r"\$__[A-Za-z0-9_]+", raw)
            self.assertEqual(macros, [], f"Found Grafana macros in {fname}: {macros}")
            
            dollars = re.findall(r"\$[A-Za-z0-9_]+", raw)
            self.assertEqual(dollars, [], f"Found unresolved dollar variables in {fname}: {dollars}")
            
            backticks = re.findall(r"`[^`]*`", raw)
            self.assertEqual(backticks, [], f"Found backticks in {fname}: {backticks}")

    def test_regex_star_meaning_wildcard(self):
        for fname, _, _ in FILES:
            with open(DASHBOARDS_DIR / fname, "r", encoding="utf-8") as f:
                data = json.load(f)
            vars_list = data.get("main", {}).get("vars", [])
            for v in vars_list:
                if v.get("includeStar"):
                    sm = v.get("extend", {}).get("starMeaning")
                    self.assertEqual(sm, ".*", f"Variable {v.get('code')} in {fname} has invalid starMeaning: {sm}")

    def test_promql_bracket_balancing(self):
        pairs = {")": "(", "]": "[", "}": "{"}
        for fname, _, _ in FILES:
            with open(DASHBOARDS_DIR / fname, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            queries = []
            def walk(obj):
                if isinstance(obj, dict):
                    if "query" in obj and isinstance(obj["query"], dict) and "q" in obj["query"]:
                        queries.append(obj["query"]["q"])
                    for val in obj.values():
                        walk(val)
                elif isinstance(obj, list):
                    for item in obj:
                        walk(item)
            walk(data)
            
            for q in queries:
                stack = []
                for ch in q:
                    if ch in "([{":
                        stack.append(ch)
                    elif ch in ")]}":
                        self.assertTrue(len(stack) > 0 and stack[-1] == pairs[ch], f"Unbalanced in {fname}: {q}")
                        stack.pop()
                self.assertEqual(len(stack), 0, f"Unbalanced query in {fname}: {q}")


if __name__ == "__main__":
    unittest.main()
