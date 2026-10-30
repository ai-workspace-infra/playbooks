"""Contract tests for the reviewed V6 Flux release graph."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
FILES = ROOT / "roles/vhosts/vault_jwt_workload_identity/files"
sys.path.insert(0, str(FILES))
SPEC = spec_from_file_location("flux_release", FILES / "flux_release.py")
FLUX = module_from_spec(SPEC)
SPEC.loader.exec_module(FLUX)


class ReleaseGraph(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.checkout = Path(self.temp.name).resolve()
        self.root_path = self.checkout / "environments/clusters/prod-k3s-v6"
        self.root_path.mkdir(parents=True)
        self.service_root = self.checkout / "services/prod-k3s-v6"
        self.service_root.mkdir(parents=True)

        source_docs = [{
            "apiVersion": "source.toolkit.fluxcd.io/v1",
            "kind": "GitRepository",
            "metadata": {"name": "config-prod", "namespace": "flux-system"},
            "spec": {"suspend": False, "url": "https://github.com/ai-workspace-infra/gitops.git",
                     "ref": {"tag": "v2026.10.30.2"}},
        }]
        units = ("vault-prod", "postgresql-prod", "ingress-prod", "observability-prod", "web-saas-prod")
        for unit in units:
            source_docs.append({
                "apiVersion": "source.toolkit.fluxcd.io/v1",
                "kind": "GitRepository",
                "metadata": {"name": f"{unit}-config", "namespace": "flux-system"},
                "spec": {"suspend": True, "url": "https://github.com/ai-workspace-infra/gitops.git",
                         "ref": {"tag": "v2026.10.30.1"}},
            })
        (self.root_path / "source.yaml").write_text(yaml.safe_dump(source_docs[0]))
        (self.root_path / "unit-sources.yaml").write_text(yaml.safe_dump_all(source_docs[1:]))

        self.root = {
            "apiVersion": "kustomize.toolkit.fluxcd.io/v1",
            "kind": "Kustomization",
            "metadata": {"name": "prod-k3s", "namespace": "flux-system"},
            "spec": {"suspend": False, "path": "./environments/clusters/prod-k3s-v6",
                     "sourceRef": {"kind": "GitRepository", "name": "config-prod"}},
        }
        (self.root_path / "root.yaml").write_text(yaml.safe_dump(self.root))

        children = []
        child_names = ("external-secrets", "vault-tls-identity", "vault-tls-bootstrap",
                       "vault-prod", "postgresql-prod", "observability-prod", "web-saas-prod", "ingress-prod")
        for name in child_names:
            path = self.service_root / name
            path.mkdir(parents=True)
            source = "config-prod" if name == "external-secrets" else f"{name}-config"
            if source not in {d["metadata"]["name"] for d in source_docs}:
                source = "vault-prod-config"
            children.append({
                "apiVersion": "kustomize.toolkit.fluxcd.io/v1",
                "kind": "Kustomization",
                "metadata": {"name": name, "namespace": "flux-system"},
                "spec": {"suspend": True, "path": f"./services/prod-k3s-v6/{name}",
                         "sourceRef": {"kind": "GitRepository", "name": source}},
            })
            if name == "vault-tls-identity":
                children[-1]["spec"]["dependsOn"] = [{"name": "external-secrets"}]
            release = {
                "apiVersion": "helm.toolkit.fluxcd.io/v2",
                "kind": "HelmRelease",
                "metadata": {"name": name, "namespace": name},
                "spec": {"suspend": True},
            }
            (path / "release.yaml").write_text(yaml.safe_dump(release))
        (self.root_path / "children.yaml").write_text(yaml.safe_dump_all(children))

    def test_stable_environment_root_uses_batch_path(self):
        source, root, graph, active = FLUX.graph(self.checkout, "v2026.10.30.2", "root")
        self.assertEqual("config-prod", source["metadata"]["name"])
        self.assertEqual("prod-k3s", root["metadata"]["name"])
        self.assertEqual("./environments/clusters/prod-k3s-v6", root["spec"]["path"])
        self.assertEqual(FLUX.STAGE_ACTIVE["root"], active)
        self.assertEqual(6, sum(key[0] == "GitRepository" for key in graph))
        self.assertEqual(9, sum(key[0] == "Kustomization" for key in graph))
        self.assertEqual(8, sum(key[0] == "HelmRelease" for key in graph))

    def test_legacy_root_name_is_rejected(self):
        self.root["metadata"]["name"] = "prod-k3s-v6"
        (self.root_path / "root.yaml").write_text(yaml.safe_dump(self.root))
        with self.assertRaisesRegex(FLUX.ClusterError, "management root"):
            FLUX.graph(self.checkout, "v2026.10.30.2", "root")


if __name__ == "__main__":
    unittest.main()
