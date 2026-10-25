#!/usr/bin/env python3
"""Verified operator adapter. No workflow dispatch, Vault mutation or key files."""
import argparse
import datetime
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[1]
OWNER_PATHS = ["gitops_source_promotion.yml", "roles/platform/gitops_source_promotion",
               "scripts/run_gitops_source_promotion.py"]
TAG_RE = re.compile(r"^v[0-9][A-Za-z0-9._-]*$")
SHA_RE = re.compile(r"^[a-f0-9]{40}$")
ROOT_SOURCE_PATH = "environments/clusters/prod-web-saas-k3s-v5/source.yaml"
ROOT_DECLARATION_PATH = "environments/clusters/prod-web-saas-k3s-v5/root.yaml"
ALLOWED_PATHS = ("services/platform-observability/prod/",)


def git(directory, *args):
    return subprocess.check_output(["git", "-C", str(directory), *args], text=True).strip()


def rollback_requested(plan, rollback=False):
    marked = plan.get("rollback", False)
    if not isinstance(marked, bool) or marked != rollback:
        raise ValueError("rollback requires matching explicit CLI option and boolean plan marker")
    if rollback and (not isinstance(plan.get("rollback_reason"), str)
                     or not 1 <= len(plan["rollback_reason"]) <= 300
                     or "\n" in plan["rollback_reason"]):
        raise ValueError("rollback requires a bounded operator recovery reason")
    return rollback


def recover_forward_requested(plan, recover_forward=False):
    marked = plan.get("recover_forward", False)
    if not isinstance(marked, bool) or marked != recover_forward:
        raise ValueError("forward recovery requires matching explicit CLI option and boolean plan marker")
    if recover_forward and (plan.get("rollback", False)
            or not isinstance(plan.get("recovery_reason"), str)
            or not 1 <= len(plan["recovery_reason"]) <= 300
            or not plan["recovery_reason"].strip()
            or any(c in plan["recovery_reason"] for c in "\r\n")):
        raise ValueError("forward recovery requires a bounded reason and excludes rollback")
    return recover_forward


def verify_release(plan, gitops_root, rollback=False, recover_forward=False):
    rollback_requested(plan, rollback)
    recover_forward_requested(plan, recover_forward)
    if rollback and recover_forward:
        raise ValueError("rollback and forward recovery are mutually exclusive")
    cluster, release = plan["cluster"], plan["release"]
    repo_url = release["repo_url"]
    if repo_url != "https://github.com/ai-workspace-infra/gitops.git":
        raise ValueError("unexpected GitOps repository")
    root = Path(gitops_root).resolve()
    remote = git(root, "remote", "get-url", "origin")
    if remote not in (repo_url, "git@github.com:ai-workspace-infra/gitops.git"):
        raise ValueError("local GitOps origin differs")
    for tag, sha in ((cluster["expected_tag"], cluster["expected_sha"]),
                     (release["desired_tag"], release["desired_sha"])):
        if not TAG_RE.fullmatch(tag) or not SHA_RE.fullmatch(sha):
            raise ValueError("immutable tag and SHA are required")
        if git(root, "rev-parse", f"{tag}^{{commit}}") != sha:
            raise ValueError("local tag does not resolve to expected commit")
        refs = dict(line.split("\t", 1)[::-1] for line in git(root, "ls-remote", "origin",
                    f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}").splitlines() if line)
        resolved = refs.get(f"refs/tags/{tag}^{{}}", refs.get(f"refs/tags/{tag}"))
        if resolved != sha:
            raise ValueError("remote immutable tag does not resolve to expected commit")
    if rollback:
        if cluster["expected_sha"] == release["desired_sha"]:
            raise ValueError("rollback must select a previous distinct ancestor commit")
        try:
            git(root, "merge-base", "--is-ancestor", release["desired_sha"], cluster["expected_sha"])
        except subprocess.CalledProcessError as error:
            raise ValueError("rollback target is not a verified ancestor") from error
    if recover_forward:
        if cluster["expected_sha"] == release["desired_sha"]:
            raise ValueError("forward recovery must select a distinct descendant commit")
        try:
            git(root, "merge-base", "--is-ancestor", cluster["expected_sha"], release["desired_sha"])
        except subprocess.CalledProcessError as error:
            raise ValueError("forward recovery expected commit is not a verified ancestor of desired") from error
    paths = git(root, "diff", "--name-only", cluster["expected_sha"], release["desired_sha"]).splitlines()
    if any(p != ROOT_SOURCE_PATH and not p.startswith(ALLOWED_PATHS) for p in paths):
        raise ValueError("release snapshot carries non-observability changes")
    source = yaml.safe_load(git(root, "show", f'{release["desired_sha"]}:{ROOT_SOURCE_PATH}'))
    if (source.get("kind") != "GitRepository" or source.get("metadata", {}).get("name") != cluster["source_name"]
            or source.get("metadata", {}).get("namespace") != cluster["source_namespace"]
            or source.get("spec", {}).get("url") != repo_url
            or source.get("spec", {}).get("ref") != {"tag": release["desired_tag"]}):
        raise ValueError("desired snapshot Source does not select its reviewed tag")
    root_doc = yaml.safe_load(git(root, "show", f'{release["desired_sha"]}:{ROOT_DECLARATION_PATH}'))
    if (root_doc.get("kind") != "Kustomization" or root_doc.get("metadata", {}).get("name") != cluster["kustomization_name"]
            or root_doc.get("spec", {}).get("path") != cluster["cluster_root"]
            or root_doc.get("spec", {}).get("sourceRef", {}).get("name") != cluster["source_name"]):
        raise ValueError("desired snapshot cluster root differs")
    return paths


def verify_target(plan, inventory, now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    target = plan["target"]
    if plan.get("environment") != "prod" or not SHA_RE.fullmatch(plan.get("owner_sha", "")):
        raise ValueError("explicit PROD owner identity is required")
    for key in ("host", "instance_id", "ssh_user"):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", str(target.get(key, ""))):
            raise ValueError("invalid target identity")
    if not re.fullmatch(r"[a-f0-9]{32}", target.get("machine_id", "")):
        raise ValueError("exact machine identity is required")
    verified = datetime.datetime.fromisoformat(target["verified_at"].replace("Z", "+00:00"))
    age = (now - verified).total_seconds()
    if not -60 <= age <= 3600:
        raise ValueError("target verification is not fresh")
    hosts = inventory.get("_meta", {}).get("hostvars", {})
    if target["host"] not in hosts:
        raise ValueError("selected target is absent from CMDB")
    host = hosts[target["host"]]
    if (str(host.get("cmdb_instance_id")) != str(target["instance_id"])
            or host.get("cmdb_environment") != "prod"
            or host.get("ansible_user") != target["ssh_user"]):
        raise ValueError("CMDB target identity differs")
    known = target.get("known_hosts", "").strip()
    if not known or "\n" in known or known.startswith("@") or not re.search(r" (ssh-ed25519|ecdsa-sha2-nistp256|ssh-rsa) [A-Za-z0-9+/]+=*( |$)", known):
        raise ValueError("one independently verified SSH host-key line is required")
    address = str(host.get("ansible_host", ""))
    port = int(host.get("ansible_port", 22))
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", address) or not 1 <= port <= 65535:
        raise ValueError("invalid connection address or port")
    valid_names = {address} if port == 22 else {f"[{address}]:{port}"}
    if not set(known.split()[0].split(",")) & valid_names or not address or any(c.isspace() for c in address):
        raise ValueError("verified host key is not bound to the connection address")
    return host


def execute(plan, cmdb, gitops_root, output, apply=False, audit_report=False, rollback=False, recover_forward=False):
    if plan.get("schema_version") != "flux-source-operator-plan/v1":
        raise ValueError("unsupported operator plan")
    if git(ROOT, "rev-parse", "HEAD") != plan.get("owner_sha"):
        raise ValueError("Playbooks checkout does not match reviewed owner SHA")
    subprocess.run(["git", "-C", str(ROOT), "diff", "--quiet", "HEAD", "--", *OWNER_PATHS], check=True)
    changed_paths = verify_release(plan, gitops_root, rollback, recover_forward)
    spec = importlib.util.spec_from_file_location("cmdb_inventory", ROOT / "inventory/terraform_cmdb.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    host = verify_target(plan, module.build_inventory(cmdb))
    mode = "audit-report" if audit_report else "apply" if apply else "plan"
    direction = "rollback" if rollback else "recover-forward" if recover_forward else "forward"
    config = {**plan["cluster"], **plan["release"], "environment": "prod", "mode": mode,
              "machine_id": plan["target"]["machine_id"],
              "admin_kubeconfig": "/etc/rancher/k3s/k3s.yaml", "rollback": rollback,
              "rollback_ancestor_verified": rollback, "rollback_reason": plan.get("rollback_reason", ""),
              "recover_forward": recover_forward, "recover_forward_ancestor_verified": recover_forward,
              "recovery_reason": plan.get("recovery_reason", "")}
    if audit_report:
        config["audit_username"] = plan["target"].get("audit_username", "")
    agent_socket = os.environ.get("SSH_AUTH_SOCK", "")
    if not agent_socket or not stat.S_ISSOCK(os.stat(agent_socket).st_mode):
        raise ValueError("an authorized in-memory SSH agent is required")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    if any(output.iterdir()):
        raise ValueError("evidence output must be a new empty directory")
    with tempfile.TemporaryDirectory(prefix="flux-source-owner-") as temp:
        temp = Path(temp)
        known = temp / "known_hosts"
        known.write_text(plan["target"]["known_hosts"].strip() + "\n")
        known.chmod(0o600)
        ssh_args = ("-F /dev/null -o BatchMode=yes -o IdentityFile=none -o IdentityAgent="
                    + shlex.quote(agent_socket) + " -o StrictHostKeyChecking=yes -o UserKnownHostsFile="
                    + shlex.quote(str(known)))
        selected = {k: host[k] for k in ("ansible_host", "ansible_user", "ansible_port", "cmdb_environment", "cmdb_instance_id")}
        selected.update(ansible_python_interpreter="/usr/bin/python3", ansible_ssh_common_args=ssh_args)
        (temp / "inventory.json").write_text(json.dumps({"all": {"hosts": {plan["target"]["host"]: selected}}}))
        variables = {"gitops_promotion_target": plan["target"]["host"], "gitops_promotion_config": config,
                     "gitops_promotion_report": str(output / "observation.json"), "gitops_promotion_apply": apply,
                     "gitops_promotion_rollback": rollback, "gitops_promotion_recover_forward": recover_forward}
        (temp / "vars.json").write_text(json.dumps(variables))
        (temp / "ansible.cfg").write_text("[defaults]\nhost_key_checking = True\nretry_files_enabled = False\nlocal_tmp = "
                                           + str(temp / "local") + "\n[ssh_connection]\nssh_args = " + ssh_args + "\n")
        env = dict(os.environ, ANSIBLE_CONFIG=str(temp / "ansible.cfg"), ANSIBLE_HOST_KEY_CHECKING="True",
                   ANSIBLE_SSH_ARGS=ssh_args, ANSIBLE_NOCOLOR="1")
        env.pop("ANSIBLE_PRIVATE_KEY_FILE", None)
        result = subprocess.run(["ansible-playbook", "-i", str(temp / "inventory.json"),
                                 str(ROOT / "gitops_source_promotion.yml"), "-e", "@" + str(temp / "vars.json")],
                                env=env, capture_output=True, text=True, timeout=1100)
        (output / "playbook.log").write_text(result.stdout + result.stderr)
        if not (output / "observation.json").exists():
            (output / "observation.json").write_text(json.dumps({"schema_version": "flux-source-promotion/v1",
                "mode": mode, "result": "transport_failed_or_unknown" if apply else "transport_failed",
                "changed": "UNKNOWN" if apply else False, "direction": direction,
                "runtime_metrics_acceptance": "NOT_RUN"}) + "\n")
        (output / "controller-receipt.json").write_text(json.dumps({"owner_sha": plan["owner_sha"], "mode": mode,
            "changed_declaration_paths": changed_paths, "exit_code": result.returncode,
            "direction": direction, "rollback_ancestor_verified": rollback,
            "rollback_reason": plan.get("rollback_reason", ""),
            "recover_forward_ancestor_verified": recover_forward, "recovery_reason": plan.get("recovery_reason", ""),
            "runtime_metrics_acceptance": "NOT_RUN"}, indent=2) + "\n")
        for path in output.iterdir():
            if path.is_file(): path.chmod(0o600)
        return result.returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--cmdb", required=True)
    parser.add_argument("--gitops-root", required=True)
    parser.add_argument("--evidence-dir", required=True)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--apply", action="store_true")
    modes.add_argument("--audit-report", action="store_true")
    directions = parser.add_mutually_exclusive_group()
    directions.add_argument("--rollback", action="store_true",
                        help="Explicit ancestor rollback; plan must also contain rollback=true and recovery reason")
    directions.add_argument("--recover-forward", action="store_true",
                            help="Explicit forward recovery from a known unhealthy root; plan marker/reason required")
    args = parser.parse_args()
    try:
        return execute(json.loads(Path(args.plan).read_text()), json.loads(Path(args.cmdb).read_text()),
                       args.gitops_root, args.evidence_dir, args.apply, args.audit_report, args.rollback, args.recover_forward)
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError):
        print("Owner rejected scope, refs or target, or transport failed; no acceptance granted.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
