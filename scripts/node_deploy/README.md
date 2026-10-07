# Node deployment owner

This directory owns the host and service execution for a validated
`NodeDeployment` contract. Toolkit selects a stage, authorizes it, and passes
the contract and stage inputs. These scripts validate pinned SSH host keys,
render an ephemeral inventory, execute the selected Playbooks tag, probe live
host/service state, and perform the guarded Vault Raft or snapshot action.

The owner checkout is always passed explicitly as `owner_root`. Scripts do not
load implementation from Toolkit. Credentials remain runtime-only; generated
inventories, SSH credentials, XConnect invitations, and snapshot plaintext are
kept in runner-private temporary paths and removed by the owning step.

`vault-migration-observation` owns the token-free SSH and DNS facts used by
`migrate-auto` and returns only a recommendation plus non-secret facts. The
Toolkit controller expands that recommendation with its pure stage plan; the
owner action does not choose playbooks, tags, tokens, or confirmations.

`setup-deployment-runner` retains the historical package initialization
behavior through the explicit `disable-unattended-upgrades` policy. Callers can
select `preserve`, but migration does not silently change the existing default.
