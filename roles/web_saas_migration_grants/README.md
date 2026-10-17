# Application access after schema migration

The caller supplies an exact clean schema version, an existing non-superuser application role and an explicit reviewed list of new public tables. Grant only SELECT/INSERT/UPDATE/DELETE in one transaction with timeouts. Existing user tables, migration version state and migration audit history cannot be grant targets. This role neither changes table ownership nor grants schema creation, superuser or global/default privileges.

Invoke `apply-web-saas-migration-grants.yml` with verified inventory and explicit host. Verify runtime readiness with the actual application role after applying; restoration with a maintenance role alone does not prove application access. Apply the grants again safely after a release retry. The caller separately controls service reconciliation/restart.
