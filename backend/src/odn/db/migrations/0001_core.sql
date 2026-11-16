-- Core multi-tenant metadata schema. Every tenant-scoped table has FORCE ROW LEVEL SECURITY keyed on
-- the transaction-local setting odn.tenant_id; when it is unset, no rows are visible (fail-closed).
-- RLS guards against application bugs (missing tenant filters); the metadata DB never runs user SQL.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE OR REPLACE FUNCTION odn_current_tenant() RETURNS uuid
LANGUAGE sql STABLE AS $$
  SELECT NULLIF(current_setting('odn.tenant_id', true), '')::uuid
$$;

CREATE TABLE tenants (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug        text NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,62}$'),
  name        text NOT NULL,
  oidc_issuer text NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE users (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id    uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
  subject      text NOT NULL,
  email        text,
  display_name text,
  role         text NOT NULL DEFAULT 'member' CHECK (role IN ('member', 'data_lead', 'admin')),
  created_at   timestamptz NOT NULL DEFAULT now(),
  last_login_at timestamptz,
  UNIQUE (tenant_id, subject)
);

CREATE TABLE connections (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
  name            text NOT NULL,
  kind            text NOT NULL CHECK (kind IN ('bigquery', 'postgres')),
  -- Non-secret settings only (project, location, default dataset, host, ...).
  config          jsonb NOT NULL DEFAULT '{}'::jsonb,
  -- Reference to a secret (Secret Manager resource name); never the secret itself.
  secret_ref      text,
  allowed_objects text[] NOT NULL DEFAULT '{}',
  created_by      uuid REFERENCES users (id) ON DELETE SET NULL,
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, name)
);

CREATE TABLE audit_logs (
  id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id        uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
  user_id          uuid REFERENCES users (id) ON DELETE SET NULL,
  connection_id    uuid REFERENCES connections (id) ON DELETE SET NULL,
  occurred_at      timestamptz NOT NULL DEFAULT now(),
  source           text NOT NULL CHECK (source IN ('chat', 'explorer', 'skill', 'workspace')),
  dialect          text NOT NULL,
  sql_text         text NOT NULL,
  decision         text NOT NULL CHECK (decision IN ('allowed', 'rejected', 'failed')),
  reason           text,
  tables           text[] NOT NULL DEFAULT '{}',
  bytes_estimated  bigint,
  bytes_processed  bigint,
  rows_returned    bigint,
  duration_ms      integer,
  job_id           text
);
CREATE INDEX audit_logs_tenant_time ON audit_logs (tenant_id, occurred_at DESC);

ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE users FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON users
  USING (tenant_id = odn_current_tenant()) WITH CHECK (tenant_id = odn_current_tenant());

ALTER TABLE connections ENABLE ROW LEVEL SECURITY;
ALTER TABLE connections FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON connections
  USING (tenant_id = odn_current_tenant()) WITH CHECK (tenant_id = odn_current_tenant());

ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_logs FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON audit_logs
  USING (tenant_id = odn_current_tenant()) WITH CHECK (tenant_id = odn_current_tenant());

-- The tenant directory has no RLS (the owner must read it without BYPASSRLS, which Cloud SQL lacks);
-- the runtime role gets no direct access and can only resolve its own tenant via this function.
CREATE OR REPLACE FUNCTION odn_resolve_tenant(p_slug text, p_issuer text) RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT id FROM tenants WHERE slug = p_slug AND oidc_issuer = p_issuer
$$;
REVOKE ALL ON FUNCTION odn_resolve_tenant(text, text) FROM PUBLIC;

-- Runtime role grants. audit_logs is append-only for the application.
REVOKE ALL ON tenants, users, connections, audit_logs FROM odn_app;
GRANT USAGE ON SCHEMA public TO odn_app;
GRANT SELECT, INSERT, UPDATE ON users TO odn_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON connections TO odn_app;
GRANT SELECT, INSERT ON audit_logs TO odn_app;
GRANT EXECUTE ON FUNCTION odn_current_tenant() TO odn_app;
GRANT EXECUTE ON FUNCTION odn_resolve_tenant(text, text) TO odn_app;
