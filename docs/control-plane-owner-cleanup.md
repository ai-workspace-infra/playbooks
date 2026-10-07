# Pipeline service execution migration

This change owns k6 load execution, service acceptance probes, and same-run XConnect node observation. Toolkit chooses the environment/release, authenticates with OIDC, and calls these composite actions at a reviewed 40-character commit SHA.

XConnect Gateway/One addresses, resource IDs, and SSH users come from the current IaC outputs. The owner validates run/attempt and compares the public handoff with those facts. There is no fixed host input. The existing Role state directories remain the service installation contract; observation does not install or enroll nodes.

A successful Provider deployment is separate from service acceptance. A Cloudflare challenge cannot produce an accepted frontend asset receipt. Observation remains summary-only and is not desktop or signed-in business acceptance.

Validation: 9 dynamic target rejection/acceptance fixtures; Bash syntax and action YAML. No host connection, installation, enrollment, or runtime UAT acceptance was performed. Toolkit legacy copies remain frozen until exact owner/caller UAT receipts justify retirement.
