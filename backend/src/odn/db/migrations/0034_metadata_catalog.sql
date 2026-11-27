-- A revision changes whenever settings that affect the source identity or scope change.
ALTER TABLE connections ADD COLUMN metadata_revision uuid NOT NULL DEFAULT gen_random_uuid();

CREATE FUNCTION odn_connection_metadata_revision() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF (NEW.tenant_id, NEW.owner_user_id, NEW.kind, NEW.config, NEW.secret_ref,
      NEW.secret_ciphertext, NEW.ssh_key_ciphertext, NEW.allowed_objects)
     IS DISTINCT FROM
     (OLD.tenant_id, OLD.owner_user_id, OLD.kind, OLD.config, OLD.secret_ref,
      OLD.secret_ciphertext, OLD.ssh_key_ciphertext, OLD.allowed_objects) THEN
    NEW.metadata_revision := gen_random_uuid();
  ELSE
    NEW.metadata_revision := OLD.metadata_revision;
  END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER connection_metadata_revision BEFORE UPDATE ON connections
  FOR EACH ROW EXECUTE FUNCTION odn_connection_metadata_revision();

CREATE FUNCTION odn_current_tenant_active() RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
  tenant_status text;
BEGIN
  SELECT status INTO tenant_status FROM tenants WHERE id = odn_current_tenant() FOR SHARE;
  RETURN coalesce(tenant_status = 'active', false);
END
$$;
REVOKE ALL ON FUNCTION odn_current_tenant_active() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION odn_current_tenant_active() TO odn_app;

-- Paths are arrays, not dot-joined strings, so identifiers retain their exact spelling.
CREATE TABLE metadata_catalogs (
  connection_id uuid PRIMARY KEY REFERENCES connections (id) ON DELETE CASCADE,
  connection_revision uuid NOT NULL,
  last_success_at timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE metadata_containers (
  connection_id uuid NOT NULL REFERENCES metadata_catalogs (connection_id) ON DELETE CASCADE,
  path text[] NOT NULL CHECK (cardinality(path) > 0 AND array_position(path, NULL) IS NULL
                              AND array_position(path, '') IS NULL),
  name text NOT NULL CHECK (name <> ''),
  kind text NOT NULL,
  description text,
  observed_at timestamptz NOT NULL,
  PRIMARY KEY (connection_id, path)
);

CREATE TABLE metadata_tables (
  connection_id uuid NOT NULL,
  path text[] NOT NULL CHECK (cardinality(path) > 0 AND array_position(path, NULL) IS NULL
                              AND array_position(path, '') IS NULL),
  container_path text[] NOT NULL CHECK (cardinality(container_path) > 0),
  name text NOT NULL CHECK (name <> ''),
  kind text NOT NULL,
  description text,
  observed_at timestamptz NOT NULL,
  CHECK (path = container_path || ARRAY[name]),
  PRIMARY KEY (connection_id, path),
  FOREIGN KEY (connection_id, container_path)
    REFERENCES metadata_containers (connection_id, path) ON DELETE CASCADE
);
CREATE INDEX metadata_tables_container ON metadata_tables (connection_id, container_path, path);

CREATE TABLE metadata_fields (
  connection_id uuid NOT NULL,
  table_path text[] NOT NULL,
  path text[] NOT NULL CHECK (cardinality(path) > 0 AND array_position(path, NULL) IS NULL
                              AND array_position(path, '') IS NULL),
  ordinal integer NOT NULL CHECK (ordinal >= 0),
  data_type text NOT NULL,
  nullable boolean,
  mode text,
  description text,
  PRIMARY KEY (connection_id, table_path, path),
  FOREIGN KEY (connection_id, table_path)
    REFERENCES metadata_tables (connection_id, path) ON DELETE CASCADE
);
CREATE INDEX metadata_fields_order ON metadata_fields (connection_id, table_path, ordinal);

CREATE TABLE metadata_refresh_jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  connection_id uuid NOT NULL REFERENCES connections (id) ON DELETE CASCADE,
  connection_revision uuid NOT NULL,
  requested_by uuid REFERENCES users (id) ON DELETE SET NULL,
  status text NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'partial', 'failed', 'cancelled')),
  lease_token uuid,
  lease_until timestamptz,
  attempt integer NOT NULL DEFAULT 0 CHECK (attempt >= 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  started_at timestamptz,
  finished_at timestamptz,
  progress_done integer NOT NULL DEFAULT 0 CHECK (progress_done >= 0),
  progress_total integer CHECK (progress_total >= 0),
  error_code text
);
CREATE UNIQUE INDEX metadata_refresh_one_active ON metadata_refresh_jobs (connection_id)
  WHERE status IN ('queued', 'running');
CREATE INDEX metadata_refresh_queue ON metadata_refresh_jobs (status, lease_until, created_at);

-- Connection RLS enforces tenant and personal ownership on every metadata table.
-- Platform metadata is shared for reads; only the table owner can write it, in a tenant context.
DO $$
DECLARE
  tbl text;
  visible text;
  tenant_write text := 'EXISTS (SELECT 1 FROM connections c WHERE c.id = connection_id '
    || 'AND c.tenant_id = odn_current_tenant())';
  platform_write text;
BEGIN
  FOREACH tbl IN ARRAY ARRAY['metadata_catalogs', 'metadata_containers', 'metadata_tables',
                              'metadata_fields', 'metadata_refresh_jobs'] LOOP
    IF tbl IN ('metadata_catalogs', 'metadata_refresh_jobs') THEN
      visible := format('EXISTS (SELECT 1 FROM connections c WHERE c.id = %I.connection_id)', tbl);
    ELSE
      visible := format(
        'EXISTS (SELECT 1 FROM connections c JOIN metadata_catalogs m ON m.connection_id = c.id '
        || 'WHERE c.id = %I.connection_id AND c.metadata_revision = m.connection_revision)', tbl);
    END IF;
    platform_write := format(
      '(current_user = pg_get_userbyid((SELECT relowner FROM pg_class WHERE oid = %L::regclass)) '
      || 'AND EXISTS (SELECT 1 FROM connections c WHERE c.id = connection_id AND c.tenant_id IS NULL))', tbl);
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', tbl);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', tbl);
    EXECUTE format('CREATE POLICY metadata_read ON %I FOR SELECT USING (%s)', tbl, visible);
    EXECUTE format('CREATE POLICY metadata_insert ON %I FOR INSERT WITH CHECK (%s OR %s)',
                   tbl, tenant_write, platform_write);
    EXECUTE format('CREATE POLICY metadata_update ON %I FOR UPDATE USING (%s OR %s) WITH CHECK (%s OR %s)',
                   tbl, tenant_write, platform_write, tenant_write, platform_write);
    EXECUTE format('CREATE POLICY metadata_delete ON %I FOR DELETE USING (%s OR %s)',
                   tbl, tenant_write, platform_write);
    EXECUTE format('REVOKE ALL ON %I FROM odn_app', tbl);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO odn_app', tbl);
  END LOOP;
END
$$;
