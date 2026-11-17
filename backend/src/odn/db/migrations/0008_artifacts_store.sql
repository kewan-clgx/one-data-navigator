-- Artifacts store: charts the agent draws and files users upload, alongside the existing result tables.
-- Both are private to their owner (tenant + user RLS); read them via tenant_transaction(tenant, user).

-- Uploaded CSV/XLSX files become queryable workspace tables of kind 'upload'.
ALTER TABLE workspace_artifacts DROP CONSTRAINT workspace_artifacts_kind_check;
ALTER TABLE workspace_artifacts ADD CONSTRAINT workspace_artifacts_kind_check
  CHECK (kind IN ('warehouse', 'workspace', 'upload'));

CREATE TABLE workspace_charts (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id           uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
  user_id             uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
  session_id          text NOT NULL CHECK (session_id ~ '^[A-Za-z0-9._:-]{1,200}$'),
  name                text NOT NULL CHECK (name ~ '^c[0-9]{1,6}$'),
  source_artifact_id  uuid REFERENCES workspace_artifacts (id) ON DELETE SET NULL,
  title               text NOT NULL DEFAULT '' CHECK (length(title) <= 200),
  chart_type          text NOT NULL CHECK (chart_type IN ('bar', 'line', 'scatter')),
  -- The rendered ChartResult (spec, aggregated points, lineage); points are capped at 500.
  result              jsonb NOT NULL,
  created_at          timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, user_id, session_id, name)
);
CREATE INDEX workspace_charts_recent ON workspace_charts (tenant_id, user_id, created_at DESC);

CREATE TABLE workspace_files (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id          uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
  user_id            uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
  session_id         text NOT NULL CHECK (session_id ~ '^[A-Za-z0-9._:-]{1,200}$'),
  filename           text NOT NULL CHECK (length(filename) BETWEEN 1 AND 255),
  mime_type          text NOT NULL CHECK (length(mime_type) <= 100),
  size_bytes         bigint NOT NULL CHECK (size_bytes >= 0),
  object_key         text NOT NULL,
  -- Set when the file was imported as a queryable workspace table (CSV/XLSX).
  table_artifact_id  uuid REFERENCES workspace_artifacts (id) ON DELETE SET NULL,
  created_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX workspace_files_recent ON workspace_files (tenant_id, user_id, created_at DESC);

ALTER TABLE workspace_charts ENABLE ROW LEVEL SECURITY;
ALTER TABLE workspace_charts FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_only ON workspace_charts
  USING (tenant_id = odn_current_tenant() AND user_id = odn_current_user())
  WITH CHECK (tenant_id = odn_current_tenant() AND user_id = odn_current_user());

ALTER TABLE workspace_files ENABLE ROW LEVEL SECURITY;
ALTER TABLE workspace_files FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_only ON workspace_files
  USING (tenant_id = odn_current_tenant() AND user_id = odn_current_user())
  WITH CHECK (tenant_id = odn_current_tenant() AND user_id = odn_current_user());

REVOKE ALL ON workspace_charts, workspace_files FROM odn_app;
GRANT SELECT, INSERT ON workspace_charts, workspace_files TO odn_app;
