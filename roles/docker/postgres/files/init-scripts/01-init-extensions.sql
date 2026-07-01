-- Keep the Contabo PostgreSQL stack aligned with jp-xhttp.svc.plus.
-- Do not install JuiceFS here.
CREATE EXTENSION IF NOT EXISTS hstore;
CREATE EXTENSION IF NOT EXISTS pgmq;
CREATE EXTENSION IF NOT EXISTS vector;
