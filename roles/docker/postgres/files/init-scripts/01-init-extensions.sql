-- PostgreSQL initialization script
-- This script runs automatically on first container startup

-- Create extensions
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_jieba;
CREATE EXTENSION IF NOT EXISTS pgmq;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS hstore;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Create a sample database for testing
CREATE DATABASE appdb;

-- Connect to the new database
\c appdb

-- Recreate extensions in the new database
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_jieba;
CREATE EXTENSION IF NOT EXISTS pgmq;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS hstore;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Create a sample schema
CREATE SCHEMA IF NOT EXISTS app;

-- Sample table with vector embeddings
CREATE TABLE IF NOT EXISTS app.documents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    embedding vector(1536),  -- OpenAI ada-002 dimension
    metadata JSONB,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Create indexes
CREATE INDEX IF NOT EXISTS idx_documents_embedding ON app.documents 
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

CREATE INDEX IF NOT EXISTS idx_documents_metadata ON app.documents 
    USING gin (metadata);

CREATE INDEX IF NOT EXISTS idx_documents_content ON app.documents 
    USING gin (to_tsvector('english', content));

-- Sample table for node management
CREATE TABLE IF NOT EXISTS app.nodes (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name TEXT NOT NULL,
    location TEXT NOT NULL,
    address TEXT NOT NULL,
    port INTEGER NOT NULL DEFAULT 443,
    server_name TEXT,
    protocols JSONB NOT NULL DEFAULT '[]'::jsonb,
    available BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Index for available nodes
CREATE INDEX IF NOT EXISTS idx_nodes_available ON app.nodes (available);

-- Sample table with Chinese full-text search
CREATE TABLE IF NOT EXISTS app.articles_zh (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    tags TEXT[],
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_articles_zh_content ON app.articles_zh 
    USING gin (to_tsvector('jiebacfg', content));

-- Sample key-value store using hstore
CREATE TABLE IF NOT EXISTS app.sessions (
    session_id TEXT PRIMARY KEY,
    data hstore NOT NULL,
    expires_at TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_expires ON app.sessions (expires_at);

-- Create a message queue
SELECT pgmq.create('task_queue');
SELECT pgmq.create('notification_queue');

-- Grant permissions (adjust as needed)
-- GRANT ALL PRIVILEGES ON SCHEMA app TO your_app_user;
-- GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA app TO your_app_user;

COMMENT ON DATABASE appdb IS 'Application database with vector search, full-text search, and message queue capabilities';
COMMENT ON SCHEMA app IS 'Main application schema';
COMMENT ON TABLE app.documents IS 'Documents with vector embeddings for semantic search';
COMMENT ON TABLE app.articles_zh IS 'Chinese articles with jieba tokenization';
COMMENT ON TABLE app.sessions IS 'Session storage using hstore';
