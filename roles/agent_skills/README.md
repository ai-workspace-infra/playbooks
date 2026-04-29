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

Before syncing, the role can materialize the skills needed by XWorkmate typical
scenario tests into the local canonical source. The default matrix includes:

| Scenario group | Skills |
| --- | --- |
| local document artifacts | `pptx`, `docx`, `xlsx`, `pdf` |
| local image processing | `image-resizer` |
| local browser automation | `browser-automation` |
| online image generation | `image-cog` |
| online image/video editing | `image-video-generation-editting`, `wan-image-video-generation-editting` |
| online video translation | `video-translator` |
| online news/search | `web-search`, `news-fetch`, `find-skills` |
| skill maintenance | `find-skills`, `self-improving`, `skill-vetter`, `skills-security-check` |

Missing local skills are installed on the Ansible controller before rsync. The
installer adapter order is:

1. `clawhub --workdir ~/.agents --dir skills --no-input install <skill>`
2. `find-skills install <skill> --target ~/.agents/skills`

Set `agent_skills_auto_install_enabled=false` to require that all skills are
already present locally. Set
`agent_skills_auto_install_fail_on_missing_installer=false` to skip missing
skills when neither installer is available; the role still fails later if a
required skill cannot be resolved.

After install, optional local quality gates run for each resolved skill when the
command exists:

- `skill-vetter <skill_path>`
- `skills-security-check <skill_path>`
- `self-improving inspect <skill_path>`

The quality gates are enabled by default and fail the play when a present gate
returns an error. Override `agent_skills_quality_gate_enabled=false` or
`agent_skills_quality_gate_fail_on_error=false` only for controlled bootstrap
environments.

Default sync excludes local runtime artifacts such as `.venv/`, `__pycache__/`,
`.pyc`, and `.DS_Store`; skills should ship source, scripts, templates, and
references rather than controller-local virtual environments.

Example:

```bash
ansible-playbook -i inventory.ini -l jp-xhttp-contabo.svc.plus deploy_agent_skills.yml
```

Bootstrap-only example that keeps the existing local source strict but skips
quality gate failures from newly installed marketplace skills:

```bash
ansible-playbook -i inventory.ini -l jp-xhttp-contabo.svc.plus deploy_agent_skills.yml \
  -e agent_skills_quality_gate_fail_on_error=false
```
