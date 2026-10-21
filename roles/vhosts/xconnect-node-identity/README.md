# XConnect node identity preflight

Resolve identity before the caller changes host configuration. Shared/plus use
the canonical runtime Vault resolver; standalone performs no credential lookup.
Team requires an explicit team/node Vault reference with node_id, team_id,
resource_class=team and token fields. Both reference segments and stored fields
must match the requested identity; missing or ambiguous identity fails closed.
No fallback to a shared credential, no Vault writes, no service restarts.

Caller inputs: node_identity_profile, node_identity_id, node_identity_team_id,
node_identity_credential_ref. Output: agent_api_token, agent_resource_class and
agent_team_id in Ansible memory, protected with no_log. The caller owns the
0600 runtime template and handler; never serialize these facts to GitOps.

This resolver does not enroll a credential in Accounts. The isolated controller
must independently accept the same dedicated credential and enforce team scope.
Do not write a production controller while validating a temporary node.

Example caller: validate_xconnect_node_identity.yml. Target selection remains
the exact reviewed CMDB host. Resolving identity has no rollback action because
it makes no persistent change.
