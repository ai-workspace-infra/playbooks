#!/usr/bin/env python3
"""Render a checksum-pinned StarsL dashboard; no cluster or Grafana writes."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import re

import yaml

SOURCE_SHA = "16a813d8c4144397553a89e99453cc98a5709cc6"
SOURCE_SHA256 = "c33eb5bd786802c2bd58e2f180e8008c468b1e9b79c5fa8887ac7680c363fba4"
SOURCE_URL = ("https://github.com/starsliao/Prometheus/blob/" + SOURCE_SHA
              + "/kubernetes/K8S%20Dashboard%20CN%2020240513%20StarsL.cn-1715536276053.json")
UID = "StarsL_cn_K8S"
CONFIGMAP = "starsl-k8s-dashboard"
FILE_KEY = "starsl-k8s-dashboard.json"
SCOPE = 'cluster=~"${cluster:regex}",environment=~"${environment:regex}"'
DS = {"type": "prometheus", "uid": "${DS_METRICS}"}


def canonical_container_selector(match):
    """Keep raw cAdvisor ownership and distinguish app containers from sandboxes."""
    metric, labels = match.groups()
    # Only application CPU/memory selectors already declaring container != ""
    # exclude pause containers. Network and id="/" host totals retain their scope.
    if (metric.startswith(("container_cpu_", "container_memory_"))
            and re.search(r'\bcontainer\s*!=\s*""', labels)
            and not re.search(r'\bcontainer\s*!=\s*"POD"', labels)):
        labels += ',container!="POD"'
    return metric + '{job="cadvisor",' + labels + '}'


def rewrite(value):
    if isinstance(value, dict):
        return {k: rewrite(v) for k, v in value.items()}
    if isinstance(value, list):
        return [rewrite(v) for v in value]
    if not isinstance(value, str):
        return value
    # Match source selectors before introducing Grafana's ${...} braces.
    value = re.sub(r"container_spec_cpu_quota\{([^}]*)\}/100000",
                   r'kube_pod_container_resource_limits{\1,resource="cpu",unit="core"}', value)
    value = re.sub(r"container_spec_memory_limit_bytes\{([^}]*)\}",
                   r'kube_pod_container_resource_limits{\1,resource="memory",unit="byte"}', value)
    # Apply to the pinned source before Grafana formatter braces are introduced.
    # KSM resource-limit replacements above are not cAdvisor metrics.
    value = re.sub(r"\b(container_[A-Za-z0-9_:]+)\{([^{}]*)\}",
                   canonical_container_selector, value)
    value = value.replace("${DS__VICTORIAMETRICS-PROD-ALL}", "${DS_METRICS}")
    value = value.replace('origin_prometheus=~"$origin_prometheus"', SCOPE)
    # Keep grouping names consistent with the new cluster selector.
    value = re.sub(r"\borigin_prometheus\b", "cluster", value)
    # Docker-only legacy labels are absent from containerd/cAdvisor samples.
    value = value.replace('image!="",', "").replace('name=~"^k8s_.*",', "")
    value = value.replace('pod=~".*$Container.*",', "")
    value = re.sub(r"\bon\s*\(pod\)", "on(pod,namespace)", value)
    value = re.sub(r"\bby\s*\(pod\)", "by(pod,namespace)", value)
    value = re.sub(r"\bkube_pod_container_info\b(?!\s*\{)",
                   "kube_pod_container_info{" + SCOPE + "}", value)
    return value


def panels(items):
    for panel in items:
        yield panel
        yield from panels(panel.get("panels", []))


def variable(name, query, **extra):
    return {"name": name, "type": "query", "label": name,
            "datasource": copy.deepcopy(DS), "query": {"query": query, "refId": name},
            "definition": query, "refresh": 1, "options": [], "current": {},
            "includeAll": False, "multi": False, **extra}


def build(source):
    raw = Path(source).read_bytes()
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise ValueError("Upstream dashboard checksum differs from the reviewed source")
    dashboard = rewrite(json.loads(raw))
    if dashboard.get("uid") != UID:
        raise ValueError("Unexpected source dashboard UID")
    dashboard.pop("id", None)
    dashboard.pop("__inputs", None)
    dashboard["editable"] = False
    dashboard["version"] = 1
    dashboard["tags"] = sorted(set(dashboard.get("tags", []) + ["starsl", "kubernetes"]))
    dashboard["description"] = (
        "StarsL K8s Dashboard; Apache-2.0; source " + SOURCE_SHA
        + ". Select datasource, cluster and environment. Missing telemetry remains No data. "
        "Secret/ConfigMap inventories and the unrelated hidden Cassandra query are omitted. "
        "PVC metrics require volume-plugin support. Source: " + SOURCE_URL)
    variables = dashboard["templating"]["list"]
    # The source's origin_prometheus selector is replaced by bounded cluster
    # and environment choices; no cluster name is committed in this dashboard.
    variables = [v for v in variables if v["name"] != "cluster"]
    for item in variables:
        item["current"] = {}
        item["options"] = []
        item["refresh"] = 1
    dashboard["templating"]["list"] = [
        {"name": "DS_METRICS", "label": "Metrics", "type": "datasource",
         "query": "prometheus", "refresh": 1,
         "current": {"text": "VictoriaMetrics", "value": "victoriametrics"}, "options": []},
        variable("cluster", "label_values(kube_node_info,cluster)"),
        variable("environment", 'label_values(kube_node_info{cluster=~"${cluster:regex}"},environment)'),
        *variables,
    ]
    for panel in panels(dashboard["panels"]):
        panel["targets"] = [t for t in panel.get("targets", [])
                            if not any(m in t.get("expr", "")
                                       for m in ("kube_secret_info", "kube_configmap_info", "cass_jvm_"))]
        defaults = panel.get("fieldConfig", {}).get("defaults", {})
        if "noValue" in defaults:
            defaults["noValue"] = "No data"
        if panel.get("id") == 51:
            panel["description"] = (
                "Only Pod/Service/workload metadata is collected. Secret/ConfigMap "
                "counts are intentionally omitted; collectors do not receive access to these resources.")
            panel["fieldConfig"]["overrides"] = [
                o for o in panel["fieldConfig"]["overrides"]
                if o.get("matcher", {}).get("options") not in ["配置", "密码"]]
            for transform in panel.get("transformations", []):
                rename = transform.get("options", {}).get("renameByName", {})
                rename.pop("Value #B", None)
                rename.pop("Value #E", None)
    return dashboard


def rendered_files(dashboard, output_dashboard=None, gitops_root=None):
    model = json.dumps(dashboard, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    files = {}
    if output_dashboard:
        files[Path(output_dashboard).resolve()] = model
    if gitops_root:
        root = Path(gitops_root).resolve()
        target = root / "services/platform-observability/prod/imported-grafana"
        if not (root / "AGENTS.md").is_file() or not (target / "kustomization.yaml").is_file():
            raise ValueError("Select an existing GitOps worktree with the imported Grafana bundle")
        cm = {"apiVersion": "v1", "kind": "ConfigMap",
              "metadata": {"name": CONFIGMAP, "namespace": "platform-observability",
                           "annotations": {"observability.svc.plus/source-sha": SOURCE_SHA,
                                           "observability.svc.plus/source-sha256": SOURCE_SHA256}},
              "data": {FILE_KEY: model}}
        files[target / (CONFIGMAP + ".yaml")] = yaml.safe_dump(cm, sort_keys=False, allow_unicode=True)
        kpath = target / "kustomization.yaml"
        kustomization = yaml.safe_load(kpath.read_text())
        resources = kustomization.setdefault("resources", [])
        if CONFIGMAP + ".yaml" not in resources:
            resources.append(CONFIGMAP + ".yaml")
        files[kpath] = yaml.safe_dump(kustomization, sort_keys=False)
        vpath = target / "source-grafana-values.yaml"
        values_cm = yaml.safe_load(vpath.read_text())
        values = yaml.safe_load(values_cm["data"]["values.yaml"])
        providers = values["grafana"]["dashboardProviders"]
        provider = next((p for p in providers if p["name"] == "import-general"), None)
        if provider is None:
            raise ValueError("Expected the existing general dashboard provider")
        if CONFIGMAP not in provider["configMaps"]:
            provider["configMaps"].append(CONFIGMAP)
        # Chart deployment annotations force a deterministic restart to pick up
        # subPath provisioning updates. The original archived inventory is not
        # rewritten or represented as containing this additional dashboard.
        bundle = {}
        for path in sorted(target.glob("*.yaml")):
            if path == vpath:
                continue  # Exclude the values ConfigMap's checksum self-reference.
            document = yaml.safe_load(path.read_text())
            if isinstance(document, dict) and document.get("kind") == "ConfigMap":
                bundle[document["metadata"]["name"]] = document.get("data", {})
        bundle[CONFIGMAP] = cm["data"]
        values["grafana"]["bundleChecksum"] = hashlib.sha256(
            json.dumps(bundle, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        values_cm["data"]["values.yaml"] = yaml.safe_dump(values, sort_keys=False, allow_unicode=True)
        files[vpath] = yaml.safe_dump(values_cm, sort_keys=False, allow_unicode=True)
    if not files:
        raise ValueError("Select an output dashboard and/or an existing GitOps worktree")
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=str(Path(__file__).with_name("upstream.json")))
    parser.add_argument("--output-dashboard")
    parser.add_argument("--gitops-root")
    parser.add_argument("--apply", action="store_true", help="Write local reviewed output files")
    args = parser.parse_args()
    dashboard = build(args.source)
    files = rendered_files(dashboard, args.output_dashboard, args.gitops_root)
    changed = [str(p) for p, body in files.items() if not p.exists() or p.read_text() != body]
    if args.apply:
        for path, body in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body)
    print(json.dumps({"uid": UID, "source_sha": SOURCE_SHA, "source_sha256": SOURCE_SHA256,
                      "changed_files": changed, "applied": args.apply}, sort_keys=True))


if __name__ == "__main__":
    main()
