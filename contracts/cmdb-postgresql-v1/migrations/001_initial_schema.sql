BEGIN;
SET LOCAL ROLE cmdb_owner;

CREATE SCHEMA IF NOT EXISTS cmdb AUTHORIZATION cmdb_owner;
SET LOCAL search_path = cmdb, pg_catalog;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now(),
    description text NOT NULL
);

CREATE TABLE IF NOT EXISTS collection_runs (
    run_id text PRIMARY KEY,
    collector text NOT NULL,
    owner_sha text NOT NULL,
    scope text NOT NULL CHECK (scope IN ('shared', 'sit', 'uat', 'prod', 'unknown')),
    account_ref text NOT NULL DEFAULT '',
    project_ref text NOT NULL DEFAULT '',
    region_ref text NOT NULL DEFAULT '',
    resource_kind text NOT NULL,
    started_at timestamptz NOT NULL,
    completed_at timestamptz,
    outcome text NOT NULL CHECK (outcome IN ('running', 'success', 'partial', 'failed')),
    scope_complete boolean NOT NULL DEFAULT false,
    resources_seen integer NOT NULL DEFAULT 0 CHECK (resources_seen >= 0),
    error_class text,
    receipt jsonb NOT NULL DEFAULT '{}'::jsonb,
    CHECK ((outcome = 'running' AND completed_at IS NULL) OR
           (outcome <> 'running' AND completed_at IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS resources (
    resource_id bigserial PRIMARY KEY,
    provider text NOT NULL,
    account_ref text NOT NULL DEFAULT '',
    project_ref text NOT NULL DEFAULT '',
    resource_kind text NOT NULL,
    native_resource_id text NOT NULL,
    canonical_id text NOT NULL UNIQUE,
    region text NOT NULL DEFAULT '',
    scope text NOT NULL CHECK (scope IN ('shared', 'sit', 'uat', 'prod', 'unknown')),
    name text NOT NULL DEFAULT '',
    provider_state text NOT NULL DEFAULT 'unknown',
    provider_state_raw text NOT NULL DEFAULT '',
    public_endpoint text,
    private_endpoint text,
    source text NOT NULL,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL,
    last_successful_run_id text NOT NULL REFERENCES collection_runs(run_id),
    lifecycle text NOT NULL DEFAULT 'present'
        CHECK (lifecycle IN ('present', 'missing_candidate', 'deletion_confirmed', 'retired')),
    attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (provider, account_ref, project_ref, resource_kind, native_resource_id)
);

CREATE TABLE IF NOT EXISTS raw_snapshots (
    snapshot_id bigserial PRIMARY KEY,
    run_id text NOT NULL REFERENCES collection_runs(run_id),
    canonical_id text,
    payload_version text NOT NULL,
    payload jsonb NOT NULL,
    payload_sha256 text NOT NULL,
    captured_at timestamptz NOT NULL DEFAULT now(),
    CHECK (canonical_id IS NULL OR length(canonical_id) > 0)
);

CREATE TABLE IF NOT EXISTS resource_observations (
    observation_id bigserial PRIMARY KEY,
    run_id text NOT NULL REFERENCES collection_runs(run_id),
    canonical_id text NOT NULL,
    observed_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    provider_state text NOT NULL,
    provider_state_raw text NOT NULL DEFAULT '',
    freshness text NOT NULL DEFAULT 'fresh'
        CHECK (freshness IN ('fresh', 'stale', 'never_observed')),
    attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (run_id, canonical_id)
);

CREATE TABLE IF NOT EXISTS desired_resources (
    desired_id bigserial PRIMARY KEY,
    gitops_ref text NOT NULL,
    resource_key text NOT NULL,
    scope text NOT NULL CHECK (scope IN ('shared', 'sit', 'uat', 'prod', 'unknown')),
    desired_config jsonb NOT NULL DEFAULT '{}'::jsonb,
    canonical_id text,
    observed_match text NOT NULL DEFAULT 'unmatched'
        CHECK (observed_match IN ('matched', 'drifted', 'declared_only', 'observed_only', 'unmatched')),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (gitops_ref, resource_key)
);

CREATE TABLE IF NOT EXISTS resource_relations (
    relation_id bigserial PRIMARY KEY,
    source_canonical_id text NOT NULL,
    relation_type text NOT NULL,
    target_canonical_id text NOT NULL,
    source_ref text NOT NULL,
    valid_from timestamptz NOT NULL DEFAULT now(),
    valid_until timestamptz,
    attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
    CHECK (valid_until IS NULL OR valid_until > valid_from),
    UNIQUE (source_canonical_id, relation_type, target_canonical_id, source_ref)
);

CREATE TABLE IF NOT EXISTS monitoring_status (
    canonical_id text PRIMARY KEY,
    monitoring_state text NOT NULL
        CHECK (monitoring_state IN ('reporting', 'missing', 'stale', 'deployment_failed', 'not_applicable', 'unknown')),
    probe_version text,
    deployment_run_id text,
    last_metrics_at timestamptz,
    last_logs_at timestamptz,
    last_verified_at timestamptz,
    receipt jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS resources_scope_kind_state_idx
    ON resources (scope, resource_kind, provider_state);
CREATE INDEX IF NOT EXISTS resources_last_seen_idx ON resources (last_seen_at DESC);
CREATE INDEX IF NOT EXISTS collection_runs_started_idx ON collection_runs (started_at DESC);
CREATE INDEX IF NOT EXISTS observations_canonical_time_idx
    ON resource_observations (canonical_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS raw_snapshots_captured_idx ON raw_snapshots (captured_at DESC);

CREATE OR REPLACE VIEW v_resource_current AS
SELECT r.canonical_id, r.provider, r.account_ref, r.project_ref, r.resource_kind,
       r.native_resource_id, r.region, r.scope, r.name, r.provider_state,
       r.provider_state_raw, r.public_endpoint, r.private_endpoint, r.source,
       r.first_seen_at, r.last_seen_at, r.lifecycle,
       CASE WHEN r.last_seen_at < now() - interval '15 minutes' THEN 'stale'
            ELSE 'fresh' END AS freshness,
       r.attributes
FROM resources AS r;

CREATE OR REPLACE VIEW v_provider_summary AS
SELECT provider, scope, resource_kind, provider_state, freshness,
       count(*)::bigint AS resource_count
FROM v_resource_current
GROUP BY provider, scope, resource_kind, provider_state, freshness;

CREATE OR REPLACE VIEW v_collection_health AS
SELECT collector, account_ref, project_ref, region_ref, resource_kind, scope,
       run_id, outcome, scope_complete, started_at, completed_at, resources_seen,
       error_class,
       CASE WHEN completed_at IS NULL THEN NULL
            ELSE extract(epoch FROM completed_at - started_at)::double precision
       END AS duration_seconds
FROM collection_runs;

CREATE OR REPLACE VIEW v_monitoring_coverage AS
SELECT r.provider, r.scope, r.resource_kind,
       count(*)::bigint AS resource_count,
       count(*) FILTER (WHERE r.provider_state = 'running')::bigint AS running_count,
       count(*) FILTER (WHERE m.monitoring_state = 'reporting')::bigint AS reporting_count,
       count(*) FILTER (WHERE m.monitoring_state IS NULL OR m.monitoring_state IN ('missing', 'stale', 'deployment_failed', 'unknown'))::bigint AS uncovered_count
FROM resources AS r
LEFT JOIN monitoring_status AS m USING (canonical_id)
WHERE r.lifecycle = 'present'
GROUP BY r.provider, r.scope, r.resource_kind;

CREATE OR REPLACE VIEW v_serverless_current AS
SELECT canonical_id, provider, account_ref, project_ref, resource_kind,
       region, scope, name, provider_state, provider_state_raw,
       public_endpoint, source, first_seen_at, last_seen_at, lifecycle,
       freshness, attributes
FROM v_resource_current
WHERE resource_kind IN ('serverless_service', 'serverless_revision', 'managed_database');

CREATE OR REPLACE VIEW v_resource_history AS
SELECT o.canonical_id, o.run_id, o.observed_at, o.received_at,
       o.provider_state, o.provider_state_raw, o.freshness, o.attributes
FROM resource_observations AS o;

INSERT INTO schema_migrations (version, description)
VALUES ('001', 'Initial provider-neutral CMDB schema and read-only views')
ON CONFLICT (version) DO NOTHING;

GRANT USAGE ON SCHEMA cmdb TO cmdb_writer, cmdb_reader, cmdb_auditor;
GRANT SELECT, INSERT, UPDATE ON collection_runs, resources, resource_observations,
    raw_snapshots, desired_resources, resource_relations, monitoring_status TO cmdb_writer;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA cmdb TO cmdb_writer;
GRANT SELECT ON v_resource_current, v_provider_summary, v_collection_health,
    v_monitoring_coverage, v_serverless_current, v_resource_history TO cmdb_reader;
GRANT SELECT ON v_collection_health, v_resource_history TO cmdb_auditor;
REVOKE ALL ON ALL TABLES IN SCHEMA cmdb FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA cmdb FROM PUBLIC;

ALTER DEFAULT PRIVILEGES FOR ROLE cmdb_owner IN SCHEMA cmdb
    GRANT SELECT, INSERT, UPDATE ON TABLES TO cmdb_writer;
ALTER DEFAULT PRIVILEGES FOR ROLE cmdb_owner IN SCHEMA cmdb
    GRANT USAGE, SELECT ON SEQUENCES TO cmdb_writer;

COMMIT;
