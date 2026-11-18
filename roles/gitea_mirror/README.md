# Gitea Repository Mirror Role

This role automates batch mirroring and continuous synchronization of GitHub repositories (both personal accounts and organizations) to Gitea (`https://gitea.com` or self-hosted Gitea instances).

## Features

- **Automated Organization Provisioning**: Detects missing organizations on Gitea (e.g. `ai-workspace-lab`) and auto-creates them prior to migration.
- **Native Gitea Background Mirror (`service_type=2`)**: Utilizes Gitea's native GitHub migration API (`POST /api/v1/repos/migrate`) with `mirror: true` so Gitea continuously pulls `main` and other branches in the background.
- **Idempotent Reconciliation**: Checks existing repositories on Gitea first, skipping previously mirrored repositories without duplicating work.
- **Zero-Secret Leakage**: Tokens are managed via Ansible environment variables / Vault and masked with `no_log: true`.

## Role Variables

| Variable | Default | Description |
| :--- | :--- | :--- |
| `gitea_mirror_api_base` | `https://gitea.com/api/v1` | Gitea API Base endpoint |
| `gitea_mirror_gitea_token` | `{{ lookup('env', 'GITEA_TOKEN') }}` | Gitea Personal Access Token with `repo` and `organization` permissions |
| `gitea_mirror_github_token` | `{{ lookup('env', 'GITHUB_TOKEN') }}` | GitHub Access Token (falls back to local `gh auth token` automatically) |
| `gitea_mirror_targets` | `['haitaopanhq', 'ai-workspace-lab', ...]` | List of personal accounts and GitHub organizations to mirror |
| `gitea_mirror_interval` | `8h` | Gitea periodic mirror pull interval |
| `gitea_mirror_recurring` | `true` | `true` for recurring background mirror sync; `false` for one-time clone |
| `gitea_mirror_dry_run` | `false` | When `true` (or in `--check` mode), previews repo lists without applying changes |
| `gitea_mirror_rate_limit_delay_seconds` | `1.0` | Throttling delay between repository migrations |

## Usage Examples

### 1. Run via Top-Level Playbook

```bash
export GITEA_TOKEN="0eb6348aeaaab418d13f954bee5b8f6371b26f67"
ansible-playbook mirror-github-to-gitea.yml
```

### 2. Dry-Run / Preview Mode

```bash
export GITEA_TOKEN="0eb6348aeaaab418d13f954bee5b8f6371b26f67"
ansible-playbook mirror-github-to-gitea.yml --check
```

### 3. Target Specific Organizations

```bash
ansible-playbook mirror-github-to-gitea.yml -e '{"gitea_mirror_targets": ["ai-workspace-lab"]}'
```

### 4. Direct Python CLI

```bash
python3 roles/gitea_mirror/files/mirror_github_to_gitea.py \
  --gitea-token "$GITEA_TOKEN" \
  --target ai-workspace-lab
```
