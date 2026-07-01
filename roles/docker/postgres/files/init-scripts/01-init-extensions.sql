-- =============================================================================
-- PostgreSQL Initialization Script
-- Runs automatically on first container startup.
--
-- CONFIGURATION ──────────────────────────────────────────────────────────────
-- All tuneable knobs live in the "Variables" section below.
-- Override them at runtime by passing -v key=value to psql, or by setting
-- environment variables that are expanded before the script is executed.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Variables
-- ---------------------------------------------------------------------------
-- Target application database
\set app_db            'appdb'

-- Application schema inside app_db (default: app)
\set app_schema        'app'

-- Extensions to install in postgres (superuser) database
-- Note: extensions must be installed per-database; the list below is
-- applied twice: once in the default postgres DB, once in app_db.
-- To disable an extension, comment it out or set the variable to empty.
\set ext_vector        'vector'
\set ext_jieba         'pg_jieba'
\set ext_pgmq          'pgmq'
\set ext_trgm          'pg_trgm'
\set ext_hstore        'hstore'
\set ext_uuid          'uuid-ossp'

-- Message queues to pre-create inside app_db
\set queue_task        'task_queue'
\set queue_notify      'notification_queue'

-- Vector embedding dimension (e.g. 1536 for OpenAI ada-002, 768 for BERT)
\set embedding_dim     1536

-- =============================================================================
-- Bootstrap extensions in the default (postgres) database
-- Keep the Contabo PostgreSQL stack aligned with jp-xhttp.svc.plus.
-- Do not install JuiceFS here.
-- =============================================================================
CREATE EXTENSION IF NOT EXISTS :"ext_vector";
CREATE EXTENSION IF NOT EXISTS :"ext_jieba";
CREATE EXTENSION IF NOT EXISTS :"ext_pgmq";
CREATE EXTENSION IF NOT EXISTS :"ext_trgm";
CREATE EXTENSION IF NOT EXISTS :"ext_hstore";
CREATE EXTENSION IF NOT EXISTS :"ext_uuid";

-- =============================================================================
-- Create application database (idempotent)
-- =============================================================================
SELECT 'CREATE DATABASE ' || quote_ident(:'app_db')
WHERE NOT EXISTS (
    SELECT 1 FROM pg_database WHERE datname = :'app_db'
) \gexec

COMMENT ON DATABASE :"app_db" IS
    'Application database with vector search, full-text search, and message queue capabilities';

-- =============================================================================
-- Switch to the application database and finish setup
-- =============================================================================
\c :"app_db"

-- ---------------------------------------------------------------------------
-- Extensions (re-installed per-database)
-- ---------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS :"ext_vector";
CREATE EXTENSION IF NOT EXISTS :"ext_jieba";
CREATE EXTENSION IF NOT EXISTS :"ext_pgmq";
CREATE EXTENSION IF NOT EXISTS :"ext_trgm";
CREATE EXTENSION IF NOT EXISTS :"ext_hstore";
CREATE EXTENSION IF NOT EXISTS :"ext_uuid";

-- ---------------------------------------------------------------------------
-- Application schema
-- ---------------------------------------------------------------------------
SELECT format('CREATE SCHEMA IF NOT EXISTS %I', :'app_schema') \gexec

SELECT format('COMMENT ON SCHEMA %I IS %L',
    :'app_schema',
    'Main application schema'
) \gexec

-- ---------------------------------------------------------------------------
-- Table: documents  (vector embeddings for semantic search)
-- ---------------------------------------------------------------------------
SELECT format($tpl$
    CREATE TABLE IF NOT EXISTS %I.documents (
        id          UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
        title       TEXT        NOT NULL,
        content     TEXT        NOT NULL,
        embedding   vector(%s),          -- configurable via :embedding_dim
        metadata    JSONB,
        created_at  TIMESTAMP   NOT NULL DEFAULT NOW(),
        updated_at  TIMESTAMP   NOT NULL DEFAULT NOW()
    )
$tpl$, :'app_schema', :'embedding_dim') \gexec

SELECT format(
    'CREATE INDEX IF NOT EXISTS idx_documents_embedding ON %I.documents
         USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)',
    :'app_schema'
) \gexec

SELECT format(
    'CREATE INDEX IF NOT EXISTS idx_documents_metadata ON %I.documents
         USING gin (metadata)',
    :'app_schema'
) \gexec

SELECT format(
    'CREATE INDEX IF NOT EXISTS idx_documents_content ON %I.documents
         USING gin (to_tsvector(''english'', content))',
    :'app_schema'
) \gexec

SELECT format(
    'COMMENT ON TABLE %I.documents IS %L',
    :'app_schema',
    'Documents with vector embeddings for semantic search'
) \gexec

-- ---------------------------------------------------------------------------
-- Table: nodes  (node / proxy server management)
-- ---------------------------------------------------------------------------
SELECT format($tpl$
    CREATE TABLE IF NOT EXISTS %I.nodes (
        id          UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
        name        TEXT        NOT NULL,
        location    TEXT        NOT NULL,
        address     TEXT        NOT NULL,
        port        INTEGER     NOT NULL DEFAULT 443,
        server_name TEXT,
        protocols   JSONB       NOT NULL DEFAULT '[]'::jsonb,
        available   BOOLEAN     NOT NULL DEFAULT TRUE,
        created_at  TIMESTAMP   NOT NULL DEFAULT NOW(),
        updated_at  TIMESTAMP   NOT NULL DEFAULT NOW()
    )
$tpl$, :'app_schema') \gexec

SELECT format(
    'CREATE INDEX IF NOT EXISTS idx_nodes_available ON %I.nodes (available)',
    :'app_schema'
) \gexec

-- ---------------------------------------------------------------------------
-- Table: articles_zh  (Chinese full-text search via jieba)
-- ---------------------------------------------------------------------------
SELECT format($tpl$
    CREATE TABLE IF NOT EXISTS %I.articles_zh (
        id          UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
        title       TEXT        NOT NULL,
        content     TEXT        NOT NULL,
        tags        TEXT[],
        created_at  TIMESTAMP   NOT NULL DEFAULT NOW()
    )
$tpl$, :'app_schema') \gexec

SELECT format(
    'CREATE INDEX IF NOT EXISTS idx_articles_zh_content ON %I.articles_zh
         USING gin (to_tsvector(''jiebacfg'', content))',
    :'app_schema'
) \gexec

SELECT format(
    'COMMENT ON TABLE %I.articles_zh IS %L',
    :'app_schema',
    'Chinese articles with jieba tokenization'
) \gexec

-- ---------------------------------------------------------------------------
-- Table: sessions  (hstore key-value session storage)
-- ---------------------------------------------------------------------------
SELECT format($tpl$
    CREATE TABLE IF NOT EXISTS %I.sessions (
        session_id  TEXT        PRIMARY KEY,
        data        hstore      NOT NULL,
        expires_at  TIMESTAMP   NOT NULL
    )
$tpl$, :'app_schema') \gexec

SELECT format(
    'CREATE INDEX IF NOT EXISTS idx_sessions_expires ON %I.sessions (expires_at)',
    :'app_schema'
) \gexec

SELECT format(
    'COMMENT ON TABLE %I.sessions IS %L',
    :'app_schema',
    'Session storage using hstore'
) \gexec

-- ---------------------------------------------------------------------------
-- Message queues
-- ---------------------------------------------------------------------------
SELECT pgmq.create(:'queue_task');
SELECT pgmq.create(:'queue_notify');

-- ---------------------------------------------------------------------------
-- Permissions (uncomment and adjust role name as needed)
-- ---------------------------------------------------------------------------
-- SELECT format('GRANT ALL PRIVILEGES ON SCHEMA %I TO your_app_user', :'app_schema') \gexec
-- SELECT format('GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA %I TO your_app_user', :'app_schema') \gexec
