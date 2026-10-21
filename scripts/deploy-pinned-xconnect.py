#!/usr/bin/env python3
"""Manual node delivery from a reviewed, immutable GitOps release contract."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

SHA = re.compile(r"[0-9a-f]{40}")
FORBIDDEN = re.compile(r"password|secret|token|private.key|database.url", re.I)


def run(argv, cwd=None, capture=False):
    return subprocess.run(argv, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE if capture else None).stdout


def inside(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("Contract path must name an existing file inside its checkout")
    return path


def revision(root, expected):
    if not SHA.fullmatch(expected):
        raise ValueError("A full immutable commit SHA is required")
    if run(["git", "rev-parse", "HEAD"], root, True).strip() != expected:
        raise ValueError("Checkout does not match the release commit")
    if run(["git", "status", "--porcelain", "--untracked-files=all"], root, True).strip():
        raise ValueError("Release checkout must be clean")


def validate(contract):
    if contract.get("schema_version") != 1:
        raise ValueError("Unsupported release contract schema")
    for key in ("playbooks_commit",):
        if not SHA.fullmatch(contract.get(key, "")):
            raise ValueError("Missing immutable " + key)
    if contract.get("profile") not in ("standalone", "shared", "plus", "team"):
        raise ValueError("Explicit node profile is required")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,252}", contract.get("node", "")):
        raise ValueError("Invalid exact inventory node")
    if contract["profile"] == "team" and not contract.get("team_id"):
        raise ValueError("Team deployment requires an explicit team scope")
    if contract["profile"] != "team" and contract.get("team_id"):
        raise ValueError("Public node must not carry team scope")
    if contract["profile"] != "standalone":
        for key in ("accounts_url", "billing_url"):
            if not re.fullmatch(r"https://[a-zA-Z0-9.-]+(?::[0-9]+)?", contract.get(key, "")):
                raise ValueError("Managed nodes require explicit HTTPS control endpoints")
    for item in contract.get("artifacts", []):
        if not item.get("tag") or item["tag"] in ("main", "latest"):
            raise ValueError("Each artifact requires a fixed release tag")
        if not re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}", item.get("checksum", "")):
            raise ValueError("Each artifact requires a checksum or digest")
    if not contract.get("artifacts") or not contract.get("steps"):
        raise ValueError("Release artifacts and owner Playbook steps are required")
    if contract["steps"][-1].get("phase") != "validate":
        raise ValueError("The final owner step must validate the deployment")
    for step in contract["steps"]:
        if step.get("phase") not in ("prepare", "deploy", "validate"):
            raise ValueError("Unsupported deployment phase")
        variables = step.get("vars", {})
        if not isinstance(variables, dict):
            raise ValueError("Step vars must be an object")
        # Credentials are resolved by the owner task using Vault, never supplied
        # in the GitOps contract or Ansible command arguments.
        if any(FORBIDDEN.search(key) for key in variables):
            raise ValueError("Credential fields are forbidden in release vars")
    return contract


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gitops", type=Path, required=True)
    parser.add_argument("--gitops-commit", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--cmdb", type=Path, help="IaC-generated CMDB for dynamic inventory")
    parser.add_argument("--mode", choices=("plan", "check", "apply"), default="plan")
    args = parser.parse_args()
    owner = Path(__file__).resolve().parents[1]
    revision(args.gitops, args.gitops_commit)
    contract = validate(json.loads(inside(args.gitops, args.manifest).read_text()))
    revision(owner, contract["playbooks_commit"])
    if args.cmdb:
        if not args.cmdb.is_file():
            raise ValueError("IaC CMDB does not exist")
        inventory = inside(owner, 'inventory/terraform_cmdb.py')
    else:
        inventory = inside(args.gitops, contract["inventory"])
    steps = [(inside(owner, step["playbook"]), step) for step in contract["steps"]]
    # Disable repository defaults that relax SSH host verification.
    env = os.environ.copy()
    env["ANSIBLE_HOST_KEY_CHECKING"] = "True"
    env["ANSIBLE_SSH_ARGS"] = "-o StrictHostKeyChecking=yes"
    if args.cmdb:
        env['AI_WORKSPACE_CMDB_JSON'] = str(args.cmdb.resolve())
    print(json.dumps({"node": contract["node"], "profile": contract["profile"],
                      "mode": args.mode, "playbooks_commit": contract["playbooks_commit"],
                      "gitops_commit": args.gitops_commit,
                      "steps": [step["phase"] for _, step in steps]}))
    if args.mode == "plan":
        return
    if contract.get('deployment_ready') is not True:
        raise ValueError('The reviewed node profile is not ready for deployment')
    commands = []
    for playbook, step in steps:
        command = ["ansible-playbook", str(playbook), "-i", str(inventory),
                   "--limit", contract["node"], "--diff",
                   "--extra-vars", json.dumps(step.get("vars", {}))]
        if args.mode == "check":
            command.append("--check")
        commands.append(command)
    # Validate every step and exact target selection before the first mutation.
    for command in commands:
        subprocess.run(command + ["--syntax-check"], cwd=owner, env=env, check=True)
        hosts = subprocess.run(command + ["--list-hosts"], cwd=owner, env=env,
                               check=True, text=True, capture_output=True)
        selected = re.findall(r"^\s+hosts \(\d+\):\s*\n((?:[ \t]+[^\n]+\n?)*)", hosts.stdout, re.M)
        if not selected or any(block.strip().split() != [contract["node"]] for block in selected):
            raise ValueError("Every owner play must select exactly the declared node")
    for command in commands:
        subprocess.run(command, cwd=owner, env=env, check=True)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as exc:
        print("Deployment stopped: " + str(exc), file=sys.stderr)
        sys.exit(1)
