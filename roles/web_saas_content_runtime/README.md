# Web SaaS Content runtime verification

Owner: Playbooks. Caller: `verify-web-saas-content-runtime.yml`. Side effects:
read-only GitOps inspection, Docker inspection, and an HTTP health probe from
the Console container. This role does not change Doco-CD selection, restart or
recreate containers, modify DNS, or touch application data.

Run after the immutable GitOps release has been selected by Doco-CD. Supply one
IaC CMDB host, the exact clean GitOps checkout and its commit SHA, plus the
generation name. The role verifies that the declared `-primary` Doco-CD target
includes the generation Content Compose overlay and that the running image,
health state, private service-token wiring, internal Console endpoint and
network isolation match that declaration. It returns only nonsecret booleans
and the public image digest; the shared token value is never printed.

Required inputs:

- `web_saas_content_target`: exact inventory host, such as `web-saas-prod-v4`.
- `web_saas_content_generation`: adjacent-generation identifier, such as `prod-v4`.
- `web_saas_content_gitops_checkout`: clean local GitOps checkout on the controller.
- `web_saas_content_gitops_sha`: exact 40-character commit SHA checked out above.

Example:

```bash
ansible-playbook -i inventory/terraform_cmdb.py \
  verify-web-saas-content-runtime.yml \
  -e web_saas_content_target=web-saas-prod-v4 \
  -e web_saas_content_generation=prod-v4 \
  -e web_saas_content_gitops_checkout=/path/to/clean/gitops \
  -e web_saas_content_gitops_sha=<verified-40-character-sha> \
  --limit web-saas-prod-v4
```

Acceptance requires the exact digest-pinned image to be healthy, all four
application containers to have the same canonical `INTERNAL_SERVICE_TOKEN`,
Console to target `http://content:8084`, and only Content to join the dedicated
egress network in addition to the internal application network. A failed gate
does not trigger an automatic repair; fix the owning GitOps or Vault-backed
runtime declaration, reconcile through Doco-CD, then rerun this verifier.

Rollback is performed by selecting a reviewed prior GitOps release through the
existing Doco-CD release-selection process. This read-only role itself has no
rollback mutation.
