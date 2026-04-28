# AI Agent Runtime

Provision a Debian-based host for AI agent and AI action execution with one
role entrypoint. The role installs:

- base tools: `curl`, `wget`, `git`, `jq`, `rsync`, `unzip`
- Node.js runtime for Playwright-based agents
- Python 3 toolchain for scripts and helpers
- system `chromium` browser
- `pandoc` + XeLaTeX PDF toolchain
- Chinese fonts for document rendering
- shared agent skills via `roles/agent_skills`

Design constraints:

- system packages are the primary source of truth
- Playwright uses system `chromium` instead of downloading browsers
- Chinese PDF rendering is treated as a runtime requirement, not an optional add-on

Default Playwright environment:

- `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1`
- `PLAYWRIGHT_BROWSERS_PATH=0`
- `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium`

Example:

```bash
ansible-playbook -i inventory.ini -l jp-xhttp-contabo.svc.plus setup-ai-agent-runtime.yml
```
