BEGIN;
SET LOCAL ROLE cmdb_owner;
SET LOCAL search_path = cmdb, pg_catalog;

-- Retired verification fixtures and confirmed retired resources remain in
-- history/detail views, but must not inflate current provider inventory.
CREATE OR REPLACE VIEW v_provider_summary AS
SELECT provider, scope, resource_kind, provider_state, freshness,
       count(*)::bigint AS resource_count
FROM v_resource_current
WHERE lifecycle = 'present'
GROUP BY provider, scope, resource_kind, provider_state, freshness;

INSERT INTO schema_migrations (version, description)
VALUES ('002', 'Exclude non-present resources from current provider summary')
ON CONFLICT (version) DO NOTHING;
COMMIT;
