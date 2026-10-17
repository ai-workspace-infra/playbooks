# PROD V2 SMTP release acceptance

The V2 Compose stack reads `/etc/xcontrol/web-saas/secrets.env`. The older
`prod-services` target reads `services.env`; repairing that file does not repair
V2. A container restart also retains the old environment. Recreate Accounts
through the reviewed immutable GitOps/Doco-CD release after rendering secrets.

Use the environment's declared Vault SMTP source, never copied credentials in
Git or a manually maintained second dotenv file. PROD currently declares
`vault://kv/prod/platform/smtp/google` with `username` and `password` keys in
`topology/prod/runtime-selection.yaml`. Cloud Run uses Secret Manager references
`smtp-username` and `smtp-password`; matching transport settings alone do not
prove matching or valid credentials.

Before and after a release, run the independent, non-delivery gate:

```sh
ansible-playbook -i /path/to/reviewed-inventory.ini check-prod-v2-smtp.yml
```

For credential recovery, set `GITOPS_CHECKOUT`,
`GITOPS_REF=refs/tags/prod-v2-YYYY.MM.DD-rN` and runtime-only `VAULT_TOKEN`,
then run `repair-prod-v2-smtp.yml` against the reviewed inventory. It reuses the
existing Vault SMTP resolver and updates only SMTP keys in the existing V2
environment file, preserving database/auth credentials. Reconcile the immutable
Accounts release with Doco-CD afterwards and rerun the gate. This step is also
required when the Vault credential version changes; an image tag alone cannot
refresh environment credentials.

This compares the persisted SMTP values against the running Accounts container,
requires encrypted submission, validates the server certificate, and performs
SMTP authentication. It sends zero messages and prints no credentials. A 535
result identifies rejected credentials; it must not be reported as successful
mail acceptance even if Accounts `/readyz` returns 200.

After that gate passes, test one explicitly authorized recipient through the
Accounts business API. `/api/auth/register/send` returns 409 for an already
verified account; use the authorized account's password recovery request when
testing that flow, without confirming or changing its password. An enumeration
safe HTTP 202 is insufficient proof on its own: verify the account exists and
the handler reached the synchronous SMTP send, inspect sanitized failure logs,
and obtain recipient receipt confirmation. Never capture OTPs/reset tokens in
release evidence.

On 2026-10-17, Selfhost transport matched Cloud Run (`smtp.gmail.com:587`,
authenticated TLS), but both credentials differed from its Secret Manager
source. The authorized recovery request returned HTTP 500 with SMTP 535.
This is a credential-source failure, not a successful delivery or a DNS issue.
