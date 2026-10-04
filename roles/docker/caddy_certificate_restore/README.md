# Caddy PEM restore

Host-only PEM restore; no Vault read, cloud resource operation, service restart,
Caddyfile change or certificate issuance. Toolkit selects one inventory host,
reads the environment-scoped Vault record and supplies a mode-0600 runtime
extra-vars file. Never upload that file as an artifact or enable secret diffs.

Inputs: `caddy_certificate_restore_target`, approved root (default
`/etc/xcontrol/tls`), one direct child directory, minimum actual leaf validity
(default 14 days), and `caddy_certificate_restore_material` with PEM strings
`fullchain`, `cert`, `key`, `ca`, `trust_bundle`.

Run `ansible-playbook -i <runtime-inventory> caddy_certificate_restore.yml
-e @<protected-runtime-vars>`. The inventory supplies the CMDB endpoint and
SSH identity (including non-root become/password transport where appropriate).
Missing/unknown/pattern targets and incomplete material fail closed.

All material is staged privately, syntax/expiry/leaf/key checked, then published
by an atomic `current` symlink. Existing immutable generations must match
exactly; they are never deleted. A changed bundle with the same leaf fingerprint
requires an explicit generation policy update, not overwrite. Validation failure
leaves the current symlink untouched. Keep old versions as a rollback point.
Serialize calls per host/domain in the control-plane workflow. Check mode only
checks inputs; it is not proof of PEM installation or live TLS acceptance.

Toolkit retains expiry/no-backup skip policy and Vault access. Later deployment
owns Caddy configuration and served-TLS verification; this role reports only
file installation. Owner tests and UAT must precede legacy Toolkit deletion.
