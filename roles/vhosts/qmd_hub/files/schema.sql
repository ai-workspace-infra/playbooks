-- QMD Hub L2 light metadata (fence journal + archive runs). Idempotent.
CREATE TABLE IF NOT EXISTS qmd_hub_changes (
  fence      bigserial PRIMARY KEY,
  origin     text NOT NULL CHECK (origin ~ '^[a-z0-9][a-z0-9._-]{0,62}$'),
  collection text NOT NULL,
  path       text NOT NULL CHECK (octet_length(path) <= 1024),
  sha256     text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  bytes      bigint NOT NULL DEFAULT 0,
  op         text NOT NULL CHECK (op IN ('upsert', 'delete')),
  at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS qmd_hub_changes_key ON qmd_hub_changes (origin, collection, path, fence DESC);
CREATE TABLE IF NOT EXISTS qmd_hub_archive_runs (
  id          bigserial PRIMARY KEY,
  from_fence  bigint NOT NULL,
  to_fence    bigint NOT NULL,
  objects     integer NOT NULL,
  quarantined integer NOT NULL,
  status      text NOT NULL CHECK (status IN ('uploaded', 'failed')),
  at          timestamptz NOT NULL DEFAULT now()
);
