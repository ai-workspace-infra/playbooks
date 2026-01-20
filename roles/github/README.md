# GitHub Organization Governance Role

This role manages GitHub Organization Rulesets to enforce branch protection and governance across all repositories within an organization.

## Governance Rules

### 1. Global Main Protection
- **Target:** `main` branch
- **Inclusion:** All repositories (`~ALL`)
- **Rules:**
  - Prevent deletion.
  - Prevent force pushes (non-fast-forward).
  - Require at least 1 approving review.
  - Dismiss stale reviews on push.

### 2. Global Release Protection
- **Target:** `release/*` branches
- **Inclusion:** All repositories (`~ALL`)
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
- `github_rulesets`: Defined in `vars/main.yml`.
