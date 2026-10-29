"""Offline release graph validation; no Kubernetes or provider calls."""
import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest

import yaml

SPEC = importlib.util.spec_from_file_location("staged", Path(__file__).resolve().parents[1] / "roles/vhosts/k3s_platform_bootstrap/files/stage_flux_declarations.py")
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


class V6Prerequisites(Graph):
    def setUp(self):
        super().setUp()
        # Use the real V6 prerequisite paths, still entirely offline.
        self.docs[1]["metadata"]["name"] = "prod-k3s-v6"
        self.docs[2]["metadata"]["name"] = "vault-prod-config"
        self.docs[3]["spec"]["sourceRef"]["name"] = "vault-prod-config"
        for child_name, source_name, path in (
            ("external-secrets", "bootstrap", STAGED.V6_ESO[1]),
            ("vault-tls-identity", "vault-prod-config", STAGED.V6_DEFERRED["vault-tls-identity"][1]),
            ("vault-tls-bootstrap", "vault-prod-config", STAGED.V6_DEFERRED["vault-tls-bootstrap"][1]),
        ):
            (self.repo / path).mkdir(parents=True)
            self.docs.append(obj("Kustomization", child_name, path=path,
                                 sourceRef={"kind": "GitRepository", "name": source_name}))
        self.leaves['external-secrets'] = [obj('HelmRelease', 'external-secrets')]
        for name, (_, path, identities) in STAGED.V6_DEFERRED.items():
            self.leaves[Path(path).name] = [
                {'apiVersion': api, 'kind': kind, 'metadata': {'namespace': ns, 'name': resource}}
                for api, kind, ns, resource in identities
            ]

    def build(self):
        return STAGED.build(self.repo, SHA, './cluster', 'prod-k3s-v6', 'bootstrap', 'flux-system', URL, self.runner)

    def test_deferred_leaves_are_validated_but_never_applied(self):
        docs = self.build()
        self.assertTrue(any(d['kind'] == 'HelmRelease' and d['metadata']['name'] == 'external-secrets' for d in docs))
        self.assertFalse(any(d['kind'] in {'ServiceAccount', 'Role', 'RoleBinding', 'ExternalSecret', 'SecretStore'} for d in docs))

    def test_missing_or_unsafe_deferred_resources_are_rejected(self):
        leaf = self.leaves['tls-bootstrap']
        leaf.append({'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': 'extra', 'namespace': 'vault-prod'}})
        with self.assertRaises(ValueError):
            self.build()
        leaf.pop()
        leaf.pop()
        with self.assertRaises(ValueError):
            self.build()

    def test_unexpected_source_or_path_is_rejected(self):
        child = next(x for x in self.docs if x['metadata']['name'] == 'vault-tls-identity')
        child['spec']['sourceRef']['name'] = 'unit-b-config'
        with self.assertRaises(ValueError):
            self.build()

    # The parent tests cover the generic graph and are not applicable to V6's
    # deliberately shared prerequisite sources or its renamed Vault source.
    test_unit_cannot_share_source_or_escape_checkout = None
    test_complete_graph_preserves_independent_release_versions = None
    test_conflicting_duplicate_or_executable_resource_is_rejected = None


if __name__ == "__main__":
    unittest.main()
