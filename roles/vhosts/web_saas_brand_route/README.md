# Isolated Console brand route

Run `reconcile_web_saas_brand.yml` with the exact target from the freshly generated Terraform CMDB. Supply `web_saas_brand_domain`, the five PEM strings in `web_saas_brand_material`, existing hostname probes, and explicit `web_saas_brand_allow_caddy_restart: true` through protected runtime variables. Certificate values must never enter logs or repository files.

This role restores only that domain's certificate through `docker/caddy_certificate_restore`. It preserves every existing Caddy route byte, validates the added block in the running Caddy image, saves the previous configuration, and restarts only the Caddy container. It verifies existing domain statuses and the unchanged identities/start times of other containers. Failure restores the previous Caddy configuration. Repeating the same operation produces no Caddy restart.

When all five installed PEM files match the supplied content, root ownership and expected permissions, restoration is skipped. Certificate hostname checks require OpenSSL's explicit match result, since `x509 -checkhost` may exit zero on a mismatch.

Rollback restores the receipt's backup configuration and restarts only Caddy. DNS and application containers are outside this role's scope.
