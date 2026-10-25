#!/usr/bin/env python3
"""Single-source Flux promotion. Captures kubectl output; emits only safe evidence."""
import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import time

TAG = re.compile(r"^v[0-9][A-Za-z0-9._-]*$")
SHA = re.compile(r"^[a-f0-9]{40}$")
ROOT_PATH = "./environments/clusters/prod-web-saas-k3s-v5"
SOURCE = "platform-config-prod"
ROOT_KUSTOMIZATION = "prod-web-saas-k3s-v5"


def validate(config):
    if config.get("environment") != "prod" or config.get("mode") not in {"plan", "apply", "audit-report"}:
        raise ValueError("unsupported environment or mode")
    if not re.fullmatch(r"[a-f0-9]{32}", config.get("machine_id", "")):
        raise ValueError("exact machine identity is required")
    if any(not TAG.fullmatch(config.get(k, "")) for k in ("expected_tag", "desired_tag")):
        raise ValueError("reviewed immutable production tags are required")
    if any(not SHA.fullmatch(config.get(k, "")) for k in ("expected_sha", "desired_sha")):
        raise ValueError("resolved immutable commits are required")
    if config.get("source_name") != SOURCE or config.get("source_namespace") != "flux-system":
        raise ValueError("unexpected Source identity")
    if config.get("kustomization_name") != ROOT_KUSTOMIZATION or config.get("cluster_root") != ROOT_PATH:
        raise ValueError("unexpected cluster root")
    for key in ("kubeconfig", "admin_kubeconfig"):
        path = config.get(key, "")
        if not isinstance(path, str) or not path.startswith("/") or "\n" in path:
            raise ValueError("explicit local kubeconfig paths are required")
    if config["admin_kubeconfig"] != "/etc/rancher/k3s/k3s.yaml":
        raise ValueError("unexpected management kubeconfig boundary")
    if config["mode"] != "audit-report" and config["kubeconfig"] != config["admin_kubeconfig"]:
        raise ValueError("management execution must use the selected host's local K3s kubeconfig")
    if config["mode"] == "audit-report" and config["kubeconfig"] == config["admin_kubeconfig"]:
        raise ValueError("audit execution requires its own kubeconfig")
    if not re.fullmatch(r"[A-Za-z0-9_.:@/-]+", config.get("context", "")):
        raise ValueError("explicit kube context is required")
    if config.get("repo_url") != "https://github.com/ai-workspace-infra/gitops.git":
        raise ValueError("unexpected GitOps repository")
    if not 10 <= int(config.get("wait_seconds", 600)) <= 900:
        raise ValueError("bounded wait must be between 10 and 900 seconds")


def ready(resource):
    return (resource.get("status", {}).get("observedGeneration") == resource.get("metadata", {}).get("generation")
            and any(c.get("type") == "Ready" and c.get("status") == "True"
                    for c in resource.get("status", {}).get("conditions", [])))


def revision_matches(revision, tag, sha):
    return revision == f"{tag}@sha1:{sha}"


def assess(config, source, root):
    if source.get("metadata", {}).get("name") != SOURCE or source.get("metadata", {}).get("namespace") != "flux-system":
        raise ValueError("live Source identity differs")
    spec = source.get("spec", {})
    expected, desired = {"tag": config["expected_tag"]}, {"tag": config["desired_tag"]}
    if spec.get("ref") not in (expected, desired) or spec.get("url") != config["repo_url"] or spec.get("suspend", False):
        raise ValueError("live Source ref/repository/suspension differs")
    rspec = root.get("spec", {})
    if (root.get("metadata", {}).get("name") != ROOT_KUSTOMIZATION
            or root.get("metadata", {}).get("namespace") != "flux-system"
            or rspec.get("path") != ROOT_PATH or rspec.get("suspend", False)
            or rspec.get("sourceRef", {}).get("name") != SOURCE
            or rspec.get("sourceRef", {}).get("kind") != "GitRepository"
            or rspec.get("sourceRef", {}).get("namespace", "flux-system") != "flux-system"):
        raise ValueError("live cluster root differs")
    observed = source.get("status", {}).get("artifact", {}).get("revision", "")
    wanted_tag, wanted_sha = ((config["desired_tag"], config["desired_sha"])
                              if spec["ref"] == desired else (config["expected_tag"], config["expected_sha"]))
    if spec["ref"] == expected and (not revision_matches(observed, wanted_tag, wanted_sha) or not ready(source)):
        raise ValueError("live Source generation/revision is not the verified baseline")
    if spec["ref"] == expected and (not ready(root) or not revision_matches(
            root.get("status", {}).get("lastAppliedRevision", ""), wanted_tag, wanted_sha)):
        raise ValueError("live cluster root is not the verified ready baseline")
    if spec["ref"] == desired and observed and not any(revision_matches(observed, tag, sha)
            for tag, sha in ((config["expected_tag"], config["expected_sha"]), (config["desired_tag"], config["desired_sha"]))):
        raise ValueError("live desired Source has an unrelated observed revision")
    return spec["ref"] == desired


def patch_for(source, config):
    version = source.get("metadata", {}).get("resourceVersion")
    if not version:
        raise ValueError("live Source has no concurrency identity")
    return [{"op": "test", "path": "/metadata/resourceVersion", "value": version},
            {"op": "test", "path": "/spec/ref", "value": source["spec"]["ref"]},
            {"op": "replace", "path": "/spec/ref", "value": {"tag": config["desired_tag"]}}]


class Kubectl:
    def __init__(self, config):
        self.prefix = ["kubectl", "--kubeconfig", config["kubeconfig"], "--context", config["context"],
                       "--request-timeout=20s"]

    def call(self, args, boolean=False):
        result = subprocess.run(self.prefix + args, capture_output=True, text=True, timeout=30)
        if boolean:
            if result.returncode in (0, 1) and result.stdout.strip() in {"yes", "no"}:
                return result.stdout.strip() == "yes"
            raise ValueError("permission self-review was unavailable")
        if result.returncode:
            raise ValueError("selected Kubernetes operation failed; raw response suppressed")
        return json.loads(result.stdout)

    def read(self, kind, name):
        return self.call(["-n", "flux-system", "get", kind, name, "-o", "json"])


def audit_permissions(client, config):
    if os.geteuid() == 0 or os.access(config["admin_kubeconfig"], os.R_OK):
        raise ValueError("audit identity must be non-root and unable to read management kubeconfig")
    identity = client.call(["auth", "whoami", "-o", "json"]).get("status", {}).get("userInfo", {}).get("username")
    if not config.get("audit_username") or identity != config["audit_username"]:
        raise ValueError("audit Kubernetes identity differs")
    allowed = [
        ["get", f"gitrepositories.source.toolkit.fluxcd.io/{SOURCE}", "-n", "flux-system"],
        ["get", f"kustomizations.kustomize.toolkit.fluxcd.io/{ROOT_KUSTOMIZATION}", "-n", "flux-system"],
    ]
    denied = [[verb, "gitrepositories.source.toolkit.fluxcd.io", "-n", "flux-system"]
              for verb in ("patch", "update", "delete", "create")]
    denied += [["patch", "kustomizations.kustomize.toolkit.fluxcd.io", "-n", "flux-system"],
               ["get", "nodes/proxy"], ["impersonate", "users"],
               ["bind", "clusterroles"], ["escalate", "clusterroles"]]
    for namespace in ("flux-system", "platform-observability", "web-saas-k3s-v5"):
        denied += [[verb, resource, "-n", namespace]
                   for verb, resource in (("get", "secrets"), ("list", "secrets"), ("get", "configmaps"),
                                          ("create", "pods/exec"), ("create", "serviceaccounts/token"))]
    if not all(client.call(["auth", "can-i", *a], boolean=True) for a in allowed):
        raise ValueError("audit status read permission missing")
    if any(client.call(["auth", "can-i", *a], boolean=True) for a in denied):
        raise ValueError("audit identity has forbidden mutation/secret/privilege access")
    return {"status": "PASS", "scope": "Kubernetes status permissions and management-kubeconfig unreadability",
            "system_privilege_audit": "NOT_RUN", "allowed_tests": len(allowed), "denied_tests": len(denied)}


def execute(config, client=None, machine_file="/etc/machine-id", sleep=time.sleep, monotonic=time.monotonic):
    validate(config)
    if Path(machine_file).read_text().strip() != config["machine_id"]:
        raise ValueError("remote machine identity differs")
    client = client or Kubectl(config)
    report = {"schema_version": "flux-source-promotion/v1", "environment": "prod", "mode": config["mode"],
              "changed": False, "result": "preflight_failed", "source": SOURCE,
              "expected_ref": {"tag": config["expected_tag"], "sha": config["expected_sha"]},
              "desired_ref": {"tag": config["desired_tag"], "sha": config["desired_sha"]},
              "runtime_metrics_acceptance": "NOT_RUN", "audit_identity_isolation": "NOT_RUN"}
    if config["mode"] == "audit-report":
        report["audit_identity_isolation"] = audit_permissions(client, config)
    source, root = client.read("gitrepository", SOURCE), client.read("kustomization", ROOT_KUSTOMIZATION)
    already = assess(config, source, root)
    report["source_revision"] = source["status"]["artifact"]["revision"]
    report["root_revision"] = root.get("status", {}).get("lastAppliedRevision")
    if config["mode"] != "apply":
        report["result"] = "planned" if config["mode"] == "plan" else "reported"
        report["would_change"] = not already
        return report
    if not already:
        report["mutation_attempted"] = True
        try:
            client.call(["-n", "flux-system", "patch", "gitrepository", SOURCE, "--type=json", "-p",
                         json.dumps(patch_for(source, config)), "-o", "json"])
        except (ValueError, OSError, subprocess.SubprocessError):
            report.update(result="mutation_failed_or_unknown", changed="UNKNOWN",
                          reason="Source patch failed or response was lost; inspect the exact Source before retry/rollback")
            return report
        report["changed"] = True
    deadline = monotonic() + int(config.get("wait_seconds", 600))
    while True:
        try:
            source, root = client.read("gitrepository", SOURCE), client.read("kustomization", ROOT_KUSTOMIZATION)
        except (ValueError, OSError, subprocess.SubprocessError):
            report.update(result="mutated_and_failed" if report["changed"] else "verification_failed",
                          reason="status read failed after promotion attempt; retain the recorded rollback ref")
            return report
        srev = source.get("status", {}).get("artifact", {}).get("revision", "")
        rrev = root.get("status", {}).get("lastAppliedRevision", "")
        if (source.get("spec", {}).get("ref") == {"tag": config["desired_tag"]}
                and ready(source) and ready(root)
                and revision_matches(srev, config["desired_tag"], config["desired_sha"])
                and revision_matches(rrev, config["desired_tag"], config["desired_sha"])):
            report.update(result="verified", source_revision=srev, root_revision=rrev)
            return report
        if monotonic() >= deadline:
            report.update(result="mutated_and_failed" if report["changed"] else "verification_failed",
                          reason="Flux did not converge to the desired immutable revision within the bounded wait")
            return report
        sleep(5)


def main():
    try:
        config = json.loads(os.environ["GITOPS_PROMOTION_CONFIG"])
        report = execute(config)
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError):
        report = {"schema_version": "flux-source-promotion/v1", "result": "preflight_failed",
                  "reason": "owner scope, identity, baseline or Kubernetes operation failed; raw response suppressed",
                  "runtime_metrics_acceptance": "NOT_RUN"}
    report["collected_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    print(json.dumps(report, sort_keys=True))
    return 0 if report["result"] in {"planned", "reported", "verified"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
