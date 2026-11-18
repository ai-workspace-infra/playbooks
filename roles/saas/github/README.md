# GitHub Organization Governance Role

This role manages GitHub Organization Rulesets to enforce branch protection and governance across all repositories within an organization.

## Governance Rules

### 1. Global Main Protection
- **Target:** `{{ github_target_branch }}` branch
- **Inclusion:** `{{ github_repository_name }}`
- **Rules:**
  - Prevent deletion.
  - Prevent force pushes (non-fast-forward).
  - Require at least 1 approving review.
  - Dismiss stale reviews on push.

### 2. Global Release Protection
- **Target:** `{{ github_release_branch_pattern }}` branches
- **Inclusion:** `{{ github_repository_name }}`
- **Rules:**
  - Prevent deletion.
  - Prevent force pushes.
  - **Enforce Linear History:** Only Cherry-pick or Rebase merges allowed.
  - Require at least 1 approving review.

## Requirements
- [GitHub CLI (gh)](https://cli.github.com/) installed on the controller.
- A `GITHUB_TOKEN` with `admin:org` permissions.

## Usage

Set your token and run the playbook:

```bash
export GITHUB_TOKEN=your_admin_token
ansible-playbook apply-branch-protection.yml
```

## Configuration
- `github_org_name`: Defined in `defaults/main.yml`.
- `github_repository_name`: Optional repository scope. Defaults to `~ALL`.
- `github_target_branch`: Main branch target. Defaults to `main`.
- `github_release_branch_pattern`: Release branch pattern. Defaults to `release/*`.
- `github_rulesets`: Defined in `vars/main.yml`.

## Common usage

Target one repository and one release branch:

```bash
export GITHUB_TOKEN=your_admin_token
ansible-playbook apply-branch-protection.yml \
  -e github_org_name=cloud-neutral \
  -e github_repository_name=xstream-vpn \
  -e github_target_branch=main \
  -e github_release_branch_pattern=release/http3-quic-stable
```

If you want the rule to apply to all repositories in the organization, keep the default `github_repository_name=~ALL`.
