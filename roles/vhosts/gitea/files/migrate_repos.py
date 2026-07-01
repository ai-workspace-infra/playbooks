#!/usr/bin/env python3
import urllib.request
import urllib.error
import json
import time
import argparse

def ensure_gitea_org(gitea_url, gitea_token, org):
    url = f"{gitea_url}/api/v1/orgs"
    headers = {"Authorization": f"token {gitea_token}", "Content-Type": "application/json"}
    payload = json.dumps({"username": org, "visibility": "public"}).encode('utf-8')
    req = urllib.request.Request(url, data=payload, headers=headers, method='POST')
    
    try:
        with urllib.request.urlopen(req) as response:
            print(f"Created org {org} in Gitea.")
    except urllib.error.HTTPError as e:
        if e.code == 422:
            print(f"Org {org} already exists in Gitea.")
        else:
            print(f"Failed to create org {org}: {e.code} {e.read().decode('utf-8')}")

def get_github_repos(github_token, org):
    url = f"https://api.github.com/orgs/{org}/repos?per_page=100"
    headers = {"Authorization": f"Bearer {github_token}", "Accept": "application/vnd.github.v3+json"}
    repos = []
    
    while url:
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req) as response:
                repos.extend(json.loads(response.read().decode('utf-8')))
                link_header = response.getheader('Link')
                url = None
                if link_header:
                    links = link_header.split(',')
                    for link in links:
                        if 'rel="next"' in link:
                            url = link.split(';')[0].strip()[1:-1]
                            break
        except urllib.error.HTTPError as e:
            print(f"Failed to get github repos for {org}: {e.code} {e.read().decode('utf-8')}")
            break
    return repos

def migrate_repo(gitea_url, gitea_token, github_token, org, repo_name, clone_url):
    url = f"{gitea_url}/api/v1/repos/migrate"
    headers = {"Authorization": f"token {gitea_token}", "Content-Type": "application/json"}
    payload = json.dumps({
        "clone_addr": clone_url,
        "auth_token": github_token,
        "repo_name": repo_name,
        "repo_owner": org,
        "mirror": False,
        "issues": True,
        "pull_requests": True,
        "wiki": True,
        "milestones": True,
        "labels": True
    }).encode('utf-8')
    
    req = urllib.request.Request(url, data=payload, headers=headers, method='POST')
    try:
        with urllib.request.urlopen(req) as response:
            print(f"Successfully migrated {org}/{repo_name}")
    except urllib.error.HTTPError as e:
        if e.code == 409:
            print(f"Repo {org}/{repo_name} already exists in Gitea.")
        else:
            print(f"Failed to migrate {org}/{repo_name}: {e.code} {e.read().decode('utf-8')}")

def main():
    parser = argparse.ArgumentParser(description="Bulk migrate GitHub orgs to Gitea")
    parser.add_argument("--github-token", required=True, help="GitHub Personal Access Token")
    parser.add_argument("--gitea-token", required=True, help="Gitea API Token")
    parser.add_argument("--gitea-url", default="http://localhost:3001", help="Gitea server URL (default: http://localhost:3001)")
    parser.add_argument("--orgs", required=True, help="Comma-separated list of GitHub organizations to migrate")
    
    args = parser.parse_args()
    orgs = [o.strip() for o in args.orgs.split(',') if o.strip()]
    
    for org in orgs:
        print(f"--- Processing {org} ---")
        ensure_gitea_org(args.gitea_url, args.gitea_token, org)
        repos = get_github_repos(args.github_token, org)
        print(f"Found {len(repos)} repositories in GitHub org {org}.")
        
        for repo in repos:
            print(f"Migrating {repo['name']}...")
            migrate_repo(args.gitea_url, args.gitea_token, args.github_token, org, repo['name'], repo['clone_url'])
            time.sleep(1)

if __name__ == "__main__":
    main()
