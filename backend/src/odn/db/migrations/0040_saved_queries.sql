-- Saved SQL: each user's SQL repository (docs/sql_repository_plan.md). Private to the owner (tenant + user RLS),
-- editable, independent of conversations. The last 20 saves of each query are kept for restore.

CREATE TABLE saved_queries (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
  user_id        uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
  -- A file-style name, shown and downloaded as "<name>.sql".
  name           text NOT NULL CHECK (name ~ '^[a-z0-9][a-z0-9_-]{0,79}$'),
  description    text NOT NULL DEFAULT '' CHECK (length(description) <= 300),
  sql_text       text NOT NULL CHECK (length(sql_text) BETWEEN 1 AND 100000),
  -- The connection it runs on; NULL for workspace SQL (q1, q2, ...) or when the connection was deleted.
  connection_id  uuid REFERENCES connections (id) ON DELETE SET NULL,
  dialect        text NOT NULL CHECK (dialect IN ('bigquery', 'postgres', 'snowflake', 'mysql', 'oracle', 'duckdb')),
  -- Tables the SQL reads (parsed at save time), for search and the Explorer count.
  tables         text[] NOT NULL DEFAULT '{}',
  version        integer NOT NULL DEFAULT 1 CHECK (version >= 1),
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, user_id, name)
);
CREATE INDEX saved_queries_recent ON saved_queries (tenant_id, user_id, updated_at DESC);
CREATE INDEX saved_queries_tables ON saved_queries USING gin (tables);

CREATE TABLE saved_query_versions (
  query_id   uuid NOT NULL REFERENCES saved_queries (id) ON DELETE CASCADE,
  tenant_id  uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
  user_id    uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
  version    integer NOT NULL,
  sql_text   text NOT NULL,
  saved_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (query_id, version)
);

ALTER TABLE saved_queries ENABLE ROW LEVEL SECURITY;
ALTER TABLE saved_queries FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_only ON saved_queries
  USING (tenant_id = odn_current_tenant() AND user_id = odn_current_user())
  WITH CHECK (tenant_id = odn_current_tenant() AND user_id = odn_current_user());

ALTER TABLE saved_query_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE saved_query_versions FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_only ON saved_query_versions
  USING (tenant_id = odn_current_tenant() AND user_id = odn_current_user())
  WITH CHECK (tenant_id = odn_current_tenant() AND user_id = odn_current_user());

REVOKE ALL ON saved_queries, saved_query_versions FROM odn_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON saved_queries, saved_query_versions TO odn_app;
