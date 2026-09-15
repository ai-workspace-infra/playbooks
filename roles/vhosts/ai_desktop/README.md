# Minimal AI Desktop

Installs the repository's minimal XFCE desktop and optional XRDP access. The
`deploy_ai_desktop.yml` entrypoint targets the generated
`ai_aggregator_cpa` inventory group and enables the existing `node_exporter`
role as basic monitoring. This role does not install AI agents, containers, or
proxy services.

Set `ai_desktop_user_password` through an encrypted variable when
`ai_desktop_manage_user` is enabled. For UAT, inject it from Vault only for
the Ansible process. Do not put it in Git, inventory, systemd units, or CI
logs. CPA OAuth bundles are separate credentials and must not be reused as
the XRDP password.
