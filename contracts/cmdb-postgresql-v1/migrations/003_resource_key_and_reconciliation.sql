BEGIN;
SET LOCAL ROLE cmdb_owner;
SET LOCAL search_path = cmdb, pg_catalog;

-- Expose authoritative resource_key, runtime_form, environment, last_observed_at,
-- and observation_status while keeping all existing v_resource_current columns intact.
CREATE OR REPLACE VIEW v_resource_current AS
SELECT r.canonical_id, r.provider, r.account_ref, r.project_ref, r.resource_kind,
       r.native_resource_id, r.region, r.scope, r.name, r.provider_state,
       r.provider_state_raw, r.public_endpoint, r.private_endpoint, r.source,
       r.first_seen_at, r.last_seen_at, r.lifecycle,
       CASE WHEN r.last_seen_at < now() - interval '15 minutes' THEN 'stale'
            ELSE 'fresh' END AS freshness,
       r.attributes,
       coalesce(
           nullif(r.attributes->>'resource_key', ''),
           r.provider || ':' || coalesce(nullif(r.project_ref, ''), nullif(r.account_ref, ''), 'default') || ':' || coalesce(nullif(r.region, ''), 'global') || ':' || r.native_resource_id
       ) AS resource_key,
       coalesce(
           nullif(r.attributes->>'runtime_form', ''),
           CASE WHEN r.resource_kind IN ('serverless_service', 'serverless_revision', 'managed_database', 'serverless') THEN 'serverless'
                WHEN coalesce(r.attributes->>'deployment_mode', '') = 'kubernetes' OR coalesce(r.attributes->>'k8s_cluster_name', '') <> '' THEN 'k3s_node'
                ELSE 'vm' END
       ) AS runtime_form,
       coalesce(nullif(r.attributes->>'environment', ''), r.scope) AS environment,
       r.last_seen_at AS last_observed_at,
       CASE WHEN r.lifecycle IN ('deletion_confirmed', 'retired') THEN 'deleted'
            WHEN r.lifecycle = 'missing_candidate' THEN 'unobserved'
            WHEN coalesce(r.attributes->>'observation_status', '') = 'collection_failed' THEN 'collection_failed'
            ELSE 'active' END AS observation_status
FROM resources AS r;

-- Exclude deleted/retired serverless resources from current serverless inventory.
CREATE OR REPLACE VIEW v_serverless_current AS
SELECT canonical_id, provider, account_ref, project_ref, resource_kind,
       region, scope, name, provider_state, provider_state_raw,
       public_endpoint, source, first_seen_at, last_seen_at, lifecycle,
       freshness, attributes, resource_key, runtime_form, environment,
       last_observed_at, observation_status
FROM v_resource_current
WHERE resource_kind IN ('serverless_service', 'serverless_revision', 'managed_database')
  AND lifecycle = 'present';

-- Reconciliation summary view aligning active/deleted/collection_failed counts with Grafana CMDB queries.
CREATE OR REPLACE VIEW v_reconciliation_summary AS
SELECT provider, scope, environment, resource_kind, runtime_form,
       count(*) FILTER (WHERE lifecycle = 'present')::bigint AS active_count,
       count(*) FILTER (WHERE lifecycle = 'present' AND observation_status = 'active')::bigint AS observed_active_count,
       count(*) FILTER (WHERE lifecycle = 'present' AND observation_status = 'collection_failed')::bigint AS collection_failed_count,
       count(*) FILTER (WHERE observation_status = 'unobserved')::bigint AS unobserved_count,
       count(*) FILTER (WHERE observation_status = 'deleted')::bigint AS deleted_count,
       max(last_observed_at) AS last_observed_at
FROM v_resource_current
GROUP BY provider, scope, environment, resource_kind, runtime_form;

GRANT SELECT ON v_resource_current, v_serverless_current, v_reconciliation_summary TO cmdb_reader;
GRANT SELECT ON v_reconciliation_summary TO cmdb_auditor;

INSERT INTO schema_migrations (version, description)
VALUES ('003', 'Expose resource_key, runtime_form, last_observed_at, observation_status and reconciliation summary')
ON CONFLICT (version) DO NOTHING;

COMMIT;
