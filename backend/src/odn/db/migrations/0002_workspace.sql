-- Result workspace: one row per Parquet artifact (warehouse result or DuckDB follow-up), with lineage.
CREATE TABLE workspace_artifacts (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
  user_id        uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
  session_id     text NOT NULL CHECK (session_id ~ '^[A-Za-z0-9._:-]{1,200}$'),
  name           text NOT NULL CHECK (name ~ '^q[0-9]{1,6}$'),
  kind           text NOT NULL CHECK (kind IN ('warehouse', 'workspace')),
  connection_id  uuid REFERENCES connections (id) ON DELETE SET NULL,
  parent_ids     uuid[] NOT NULL DEFAULT '{}',
  sql_text       text NOT NULL,
  source_tables  text[] NOT NULL DEFAULT '{}',
  object_key     text NOT NULL,
  row_count      bigint NOT NULL,
  arrow_bytes    bigint NOT NULL,
  truncated      boolean NOT NULL,
  columns        jsonb NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, user_id, session_id, name)
);
CREATE INDEX workspace_artifacts_session ON workspace_artifacts (tenant_id, user_id, session_id, created_at);

ALTER TABLE workspace_artifacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE workspace_artifacts FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON workspace_artifacts
  USING (tenant_id = odn_current_tenant()) WITH CHECK (tenant_id = odn_current_tenant());

REVOKE ALL ON workspace_artifacts FROM odn_app;
GRANT SELECT, INSERT ON workspace_artifacts TO odn_app;
