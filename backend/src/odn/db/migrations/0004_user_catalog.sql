-- Per-user favorites and table usage, used to rank search results. Paths are lowercase and fully qualified.
CREATE TABLE user_favorites (
  tenant_id      uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
  user_id        uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
  connection_id  uuid NOT NULL REFERENCES connections (id) ON DELETE CASCADE,
  object_path    text NOT NULL CHECK (length(object_path) BETWEEN 1 AND 1024),
  kind           text NOT NULL CHECK (kind IN ('dataset', 'table')),
  created_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, user_id, connection_id, object_path)
);

-- Tables referenced by the user's successful warehouse queries (explorer and chat).
CREATE TABLE user_table_usage (
  tenant_id      uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
  user_id        uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
  connection_id  uuid NOT NULL REFERENCES connections (id) ON DELETE CASCADE,
  table_path     text NOT NULL,
  query_count    integer NOT NULL DEFAULT 1,
  first_used_at  timestamptz NOT NULL DEFAULT now(),
  last_used_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, user_id, connection_id, table_path)
);
CREATE INDEX user_table_usage_recent ON user_table_usage (tenant_id, user_id, connection_id, last_used_at DESC);

ALTER TABLE user_favorites ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_favorites FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON user_favorites
  USING (tenant_id = odn_current_tenant()) WITH CHECK (tenant_id = odn_current_tenant());

ALTER TABLE user_table_usage ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_table_usage FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON user_table_usage
  USING (tenant_id = odn_current_tenant()) WITH CHECK (tenant_id = odn_current_tenant());

REVOKE ALL ON user_favorites, user_table_usage FROM odn_app;
GRANT SELECT, INSERT, DELETE ON user_favorites TO odn_app;
GRANT SELECT, INSERT, UPDATE ON user_table_usage TO odn_app;
