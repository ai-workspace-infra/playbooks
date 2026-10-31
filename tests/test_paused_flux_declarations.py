"""Offline release graph validation; no Kubernetes or provider calls."""
import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest

import yaml

SPEC = importlib.util.spec_from_file_location("staged", Path(__file__).resolve().parents[1] / "roles/vhosts/flux_gitops/files/stage_flux_declarations.py")
STAGED = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STAGED)
SHA = "a" * 40
URL = "https://example.invalid/config.git"


def obj(kind, name, **spec):
    return {"apiVersion": STAGED.FLUX_APIS[kind], "kind": kind,
            "metadata": {"name": name, "namespace": "flux-system"},
            "spec": {"suspend": True, **spec}}


class Graph(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        for name in ("cluster", "unit-a", "unit-b"):
            (self.repo / name).mkdir()
        self.docs = [obj("GitRepository", "bootstrap", url=URL, ref={"commit": SHA}),
                     obj("Kustomization", "generation-root", path="./cluster", sourceRef={"kind": "GitRepository", "name": "bootstrap"})]
        self.leaves = {}
        for unit in ("unit-a", "unit-b"):
            self.docs += [obj("GitRepository", unit+"-config", url=URL, ref={"commit": SHA}),
                          obj("Kustomization", unit, path="./"+unit, sourceRef={"kind": "GitRepository", "name": unit+"-config"})]
            self.leaves[unit] = [obj("HelmRelease", unit, chart={"spec": {"version": "1.0.0" if unit == "unit-a" else "2.3.4"}})]
        self.dirty = False

    def runner(self, *argv):
        if argv[0] == "git":
            return " M changed" if "status" in argv and self.dirty else ("" if "status" in argv else SHA)
        path = Path(argv[-1]).name
        return yaml.safe_dump_all(self.docs if path == "cluster" else self.leaves[path])

    def build(self):
        return STAGED.build(self.repo, SHA, "./cluster", "generation-root", "bootstrap", "flux-system", URL, self.runner)

    def test_complete_graph_preserves_independent_release_versions(self):
        result = self.build()
        releases = [d for d in result if d["kind"] == "HelmRelease"]
        self.assertEqual([d["spec"]["chart"]["spec"]["version"] for d in releases], ["1.0.0", "2.3.4"])
        self.assertTrue(all(d["spec"]["suspend"] for d in result))

    def test_active_release_root_or_source_is_rejected(self):
        targets = [self.docs[0], self.docs[1], self.leaves["unit-a"][0]]
        for target in targets:
            target["spec"]["suspend"] = False
            with self.assertRaises(ValueError): self.build()
            target["spec"]["suspend"] = True

    def test_branch_dirty_and_wrong_pins_are_rejected(self):
        for ref in ({"branch": "main"}, {"commit": "b"*40}):
            self.docs[0]["spec"]["ref"] = ref
            with self.assertRaises(ValueError): self.build()
        self.docs[0]["spec"]["ref"] = {"commit": SHA}
        self.dirty = True
        with self.assertRaises(ValueError): self.build()

    def test_unit_cannot_share_source_or_escape_checkout(self):
        unit = self.docs[-1]
        unit["spec"]["sourceRef"]["name"] = "unit-a-config"
        with self.assertRaises(ValueError): self.build()
        unit["spec"]["sourceRef"]["name"] = "unit-b-config"
        unit["spec"]["path"] = "../outside"
        with self.assertRaises(ValueError): self.build()

    def test_conflicting_duplicate_or_executable_resource_is_rejected(self):
        duplicate = copy.deepcopy(self.leaves["unit-a"][0])
        duplicate["spec"]["chart"]["spec"]["version"] = "9.0.0"
        self.leaves["unit-b"].append(duplicate)
        with self.assertRaises(ValueError): self.build()
        self.leaves["unit-b"] = [{"kind": "Job", "metadata": {"name": "run"}}]
        with self.assertRaises(ValueError): self.build()


if __name__ == "__main__":
    unittest.main()
