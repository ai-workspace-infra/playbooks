# AI Agent Runtime

Provision a Debian-based host for AI agent and AI action execution with one
role entrypoint. The role installs:

- base tools: `curl`, `wget`, `git`, `jq`, `rsync`, `unzip`
- Node.js runtime for Playwright-based agents
- Python 3 toolchain for scripts and helpers
- existing system browser, preferring the live `/usr/local/bin/chromium` wrapper
  or Google Chrome before installing browser packages
- `pandoc` + XeLaTeX PDF toolchain
- Chinese fonts for document rendering
- shared agent skills via `roles/agent_skills`, including the categorized
  `../xworkspace-core-skills/skills/` repository source by default

Design constraints:

- system packages are the primary source of truth
- global npm packages are managed through
  `/usr/local/sbin/ai-workspace-manage-npm-global-package` so repeated installs
  are idempotent and stale global bin links can be overwritten safely
- Playwright uses the resolved system browser instead of downloading browsers
- Chinese PDF rendering is treated as a runtime requirement, not an optional add-on

Global npm package actions:

- `install` is the default and only changes the host when a package is missing
  or an exact pinned version differs
- `reinstall` forces the configured package set back into place
- `upgrade`, `backup`, `restore`, and `migrate` are reserved action entrypoints
  for future runtime lifecycle workflows

Default Playwright environment:

- `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1`
- `PLAYWRIGHT_BROWSERS_PATH=0`
- `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/local/bin/chromium` when that live
  wrapper exists

Example:

```bash
ansible-playbook -i inventory.ini -l jp-xhttp-contabo.svc.plus setup-ai-agent-skills.yml
```

`setup-ai-agent-skills.yml` runs `roles/ai_agent_runtime`, which installs system
dependencies and syncs the current Skill catalog through the embedded
`roles/agent_skills` step in one pass.
