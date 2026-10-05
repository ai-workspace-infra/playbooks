# XConnect lab runtime role

This role is the Playbooks owner for XConnect host and service operations. It
accepts an explicit UAT operation and delegates runtime installation,
enrollment and status verification to the existing `xconnect_gateway` and
`xconnect_one` roles.

It does not run Terraform, mutate GitOps declarations, reconcile DNS or
Cloudflare, manage cloud resources, or enable a PROD target. The caller must
provide an exact inventory host and reviewed runtime inputs. The companion
`xconnect-lab-runtime.yml` playbook is therefore safe to use as the owner
execution entrypoint after the Toolkit control-plane caller has verified the
immutable refs and target.

Verification operations use fixed status commands and suppress credential-
carrying output. Invitation issuance and cloud/DNS orchestration remain
control-plane concerns until their dedicated owner migration is complete.

