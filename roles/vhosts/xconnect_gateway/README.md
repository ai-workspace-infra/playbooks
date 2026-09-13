# Zero-driven XConnect Gateway

This is the canonical OS-level Gateway role. It installs reviewed Gateway and
external Xray artifacts, initializes protected local state, exchanges a
short-lived Zero invitation, reconciles signed Gateway configuration, starts
the WireGuard/Xray data plane, and keeps it synchronized through a systemd
timer.

Together with `vhosts/xconnect_one`, this forms the two Zero-driven roles:
Gateway is the relay/service role and One is the controlled-client role.

The role does not create cloud resources, VPCs, security groups, DNS records,
or Portal/Accounts data. Binary sources, TLS material, and invitations are
runtime inputs; GitOps remains limited to non-sensitive topology and release
selection.
