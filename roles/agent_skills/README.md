# Agent Skills

Synchronizes the controller user's `~/.agents/skills/` directory to an Ubuntu
runtime user's canonical skills directory, then exposes the same directory to
agent-specific skill locations.

Default source and target:

- local source: `~/.agents/skills/`
- remote canonical path: `/home/ubuntu/.agents/skills/`
- default agent targets: `codex`, `gemini`, `opencode`, `hermers`, `openclaw`

The role keeps one remote source of truth and links each agent's skills entry to
that canonical directory. Existing non-symlink target directories are rejected by
default to avoid silently deleting agent-owned content. Set
`agent_skills_replace_existing_target_dirs=true` only when those target
directories should be replaced.

Default sync excludes local runtime artifacts such as `.venv/`, `__pycache__/`,
`.pyc`, and `.DS_Store`; skills should ship source, scripts, templates, and
references rather than controller-local virtual environments.

Example:

```bash
ansible-playbook -i inventory.ini -l jp-xhttp-contabo.svc.plus deploy_agent_skills.yml
```
