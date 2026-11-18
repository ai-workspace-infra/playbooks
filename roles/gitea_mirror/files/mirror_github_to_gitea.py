#!/usr/bin/env python3
"""
Batch Mirror GitHub Repositories (Personal + Organizations) to Gitea.com
Utilizes Gitea's Native Migration/Mirror API: POST /api/v1/repos/migrate
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

def get_github_token() -> str:
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        return token
    try:
        res = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=True)
        return res.stdout.strip()
    except Exception:
        pass
    return ""

def list_github_repos(owner: str, token: str) -> list[dict]:
    cmd = ["gh", "repo", "list", owner, "--limit", "200", "--json", "name,isPrivate,isFork,defaultBranchRef,description"]
    env = os.environ.copy()
    if token:
        env["GITHUB_TOKEN"] = token
    res = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if res.returncode != 0:
        print(f"[WARN] Failed to list repos for {owner}: {res.stderr.strip()}", file=sys.stderr)
        return []
    try:
        return json.loads(res.stdout)
    except Exception:
        return []

def gitea_request(api_base: str, method: str, path: str, token: str, data: dict | None = None) -> tuple[int, dict | str]:
    url = f"{api_base.rstrip('/')}{path}"
    headers = {
        "Authorization": f"token {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "ansible-gitea-mirror-role/1.0",
    }
    body = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read().decode("utf-8")
            try:
                return resp.status, json.loads(content)
            except Exception:
                return resp.status, content
    except urllib.error.HTTPError as e:
        err_content = e.read().decode("utf-8")
        try:
            return e.code, json.loads(err_content)
        except Exception:
            return e.code, err_content
    except Exception as e:
        return 0, str(e)

def ensure_gitea_org(api_base: str, org_name: str, token: str) -> bool:
    status, res = gitea_request(api_base, "GET", f"/orgs/{org_name}", token)
    if status == 200:
        return True
    print(f"[*] Organization '{org_name}' not found on Gitea. Creating it...")
    status, create_res = gitea_request(api_base, "POST", "/orgs", token, {
        "username": org_name,
        "full_name": org_name,
        "visibility": "public",
        "repo_admin_change_team_access": True,
    })
    if status in (201, 200):
        print(f"[+] Successfully created organization '{org_name}' on Gitea.")
        return True
    print(f"[WARN] Could not create organization '{org_name}' on Gitea (status {status}): {create_res}")
    return False

def migrate_repo_to_gitea(
    api_base: str,
    gh_owner: str,
    repo_name: str,
    default_branch: str,
    is_private: bool,
    description: str,
    gitea_owner: str,
    gitea_token: str,
    gh_token: str,
    as_mirror: bool = True,
    interval: str = "8h",
) -> tuple[str, str]:
    # Check if already exists on Gitea
    status, _ = gitea_request(api_base, "GET", f"/repos/{gitea_owner}/{repo_name}", gitea_token)
    if status == 200:
        return "skipped", "Already exists on Gitea"

    payload = {
        "service": "github",
        "clone_addr": f"https://github.com/{gh_owner}/{repo_name}.git",
        "auth_token": gh_token,
        "repo_name": repo_name,
        "repo_owner": gitea_owner,
        "mirror": as_mirror,
        "mirror_interval": interval,
        "private": is_private,
        "description": description or f"Mirrored from GitHub {gh_owner}/{repo_name}",
        "wiki": False,
        "milestones": False,
        "labels": False,
        "issues": False,
        "pull_requests": False,
        "releases": False,
    }

    status, res = gitea_request(api_base, "POST", "/repos/migrate", gitea_token, payload)
    if status in (201, 200):
        return "created", f"Mirror scheduled (branch: {default_branch})"

    # Handle Gitea instances where pull mirror is disabled by admin (e.g. gitea.com public instance)
    if status == 403 and isinstance(res, dict) and "disabled the creation of new pull mirrors" in res.get("message", ""):
        payload["mirror"] = False
        payload.pop("mirror_interval", None)
        fallback_status, fallback_res = gitea_request(api_base, "POST", "/repos/migrate", gitea_token, payload)
        if fallback_status in (201, 200):
            return "created", f"Imported latest code (pull mirror disabled on {api_base}, fallback to import)"
        return "failed", f"HTTP {fallback_status}: {fallback_res}"

    return "failed", f"HTTP {status}: {res}"

def main():
    parser = argparse.ArgumentParser(description="Mirror GitHub repositories to Gitea.com")
    parser.add_argument("--gitea-api", default="https://gitea.com/api/v1", help="Gitea API base URL")
    parser.add_argument("--gitea-token", help="Gitea API Personal Access Token")
    parser.add_argument("--gh-token", help="GitHub Personal Access Token (defaults to `gh auth token`)")
    parser.add_argument("--target", action="append", help="Specific owner/org to mirror (can be repeated)")
    parser.add_argument("--repo", help="Specific repo name to mirror (filters repos across targets)")
    parser.add_argument("--interval", default="8h", help="Mirror interval (e.g. 4h, 8h, 24h)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without making changes")
    parser.add_argument("--no-mirror", action="store_true", help="One-time import instead of continuous mirror")
    parser.add_argument("--delay", type=float, default=1.0, help="Delay seconds between migrations")
    args = parser.parse_args()

    gh_token = args.gh_token or get_github_token()
    gitea_token = args.gitea_token or os.environ.get("GITEA_TOKEN")

    targets = args.target if args.target else [
        "haitaopanhq",
        "ai-workspace-lab",
        "ai-workspace-services",
        "ai-workspace-infra",
        "ai-workspace-xstream",
        "ai-desktop-lab",
    ]

    if not args.dry_run and not gitea_token:
        print("Error: Gitea Token is required. Pass --gitea-token or set GITEA_TOKEN.", file=sys.stderr)
        sys.exit(1)

    print("=" * 60)
    print("  GitHub -> Gitea Mirror Reconciliation")
    print(f"  Gitea API:   {args.gitea_api}")
    print(f"  Targets:     {', '.join(targets)}")
    print(f"  Mirror Mode: {'One-time import' if args.no_mirror else f'Recurring mirror ({args.interval})'}")
    print(f"  Dry Run:     {args.dry_run}")
    print("=" * 60)

    # 1. Fetch current user from Gitea
    current_gitea_user = ""
    if not args.dry_run:
        u_status, u_info = gitea_request(args.gitea_api, "GET", "/user", gitea_token)
        if u_status == 200 and isinstance(u_info, dict):
            current_gitea_user = u_info.get("username", "")
            print(f"[+] Authenticated on Gitea as: {current_gitea_user}")
        else:
            print(f"[ERROR] Failed to authenticate on Gitea (status {u_status}): {u_info}", file=sys.stderr)
            sys.exit(1)

    # 2. Plan repositories
    plan = []
    for target in targets:
        repos = list_github_repos(target, gh_token)
        if args.repo:
            repos = [r for r in repos if r.get("name") == args.repo]
        print(f"\n[Target: {target}] Found {len(repos)} repositories")
        for r in repos:
            name = r.get("name")
            branch = (r.get("defaultBranchRef") or {}).get("name", "main")
            priv = r.get("isPrivate", False)
            desc = r.get("description", "")
            plan.append({
                "gh_owner": target,
                "repo_name": name,
                "default_branch": branch,
                "is_private": priv,
                "description": desc,
                "gitea_owner": target,
            })
            print(f"  - {target}/{name} (branch: {branch}, private: {priv})")

    print(f"\nTotal repositories to process: {len(plan)}")

    if args.dry_run:
        print("\n[Dry Run] No remote changes submitted.")
        print(json.dumps({"dry_run": True, "total": len(plan)}, indent=2))
        return

    # 3. Ensure Organizations exist on Gitea
    for target in targets:
        if target != current_gitea_user:
            ensure_gitea_org(args.gitea_api, target, gitea_token)

    # 4. Execute Migration
    created = 0
    skipped = 0
    failed = 0
    results = []

    print("\nStarting repository migration pipeline...")
    for idx, item in enumerate(plan, 1):
        gh_owner = item["gh_owner"]
        repo_name = item["repo_name"]
        branch = item["default_branch"]
        priv = item["is_private"]
        desc = item["description"]
        gitea_owner = item["gitea_owner"]

        print(f"[{idx}/{len(plan)}] {gh_owner}/{repo_name} -> {gitea_owner}/{repo_name} ...", end=" ", flush=True)
        status, message = migrate_repo_to_gitea(
            api_base=args.gitea_api,
            gh_owner=gh_owner,
            repo_name=repo_name,
            default_branch=branch,
            is_private=priv,
            description=desc,
            gitea_owner=gitea_owner,
            gitea_token=gitea_token,
            gh_token=gh_token,
            as_mirror=not args.no_mirror,
            interval=args.interval,
        )
        print(f"[{status.upper()}] {message}")

        if status == "created":
            created += 1
        elif status == "skipped":
            skipped += 1
        else:
            failed += 1

        results.append({
            "repo": f"{gh_owner}/{repo_name}",
            "status": status,
            "message": message,
        })
        time.sleep(args.delay)

    print("\n" + "=" * 60)
    print(f"Summary: Total: {len(plan)} | Created: {created} | Skipped: {skipped} | Failed: {failed}")
    print("=" * 60)

if __name__ == "__main__":
    main()
