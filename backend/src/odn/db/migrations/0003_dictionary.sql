-- Curated data dictionary: tenant-scoped business definitions for approved tables/fields.
-- Source descriptions stay in the warehouse (read live via the connector); only curated text is stored here.
CREATE TABLE data_definitions (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
  connection_id  uuid NOT NULL REFERENCES connections (id) ON DELETE CASCADE,
  table_path     text NOT NULL,
  -- '' means the definition is for the table itself.
  field_path     text NOT NULL DEFAULT '',
  definition     text NOT NULL CHECK (length(definition) BETWEEN 1 AND 4000),
  status         text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'approved', 'deprecated')),
  updated_by     uuid REFERENCES users (id) ON DELETE SET NULL,
  updated_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, connection_id, table_path, field_path)
);

CREATE TABLE data_definition_history (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id      uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
  definition_id  uuid NOT NULL REFERENCES data_definitions (id) ON DELETE CASCADE,
  definition     text NOT NULL,
  status         text NOT NULL,
  changed_by     uuid REFERENCES users (id) ON DELETE SET NULL,
  changed_at     timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE data_definitions ENABLE ROW LEVEL SECURITY;
ALTER TABLE data_definitions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON data_definitions
  USING (tenant_id = odn_current_tenant()) WITH CHECK (tenant_id = odn_current_tenant());

ALTER TABLE data_definition_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE data_definition_history FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON data_definition_history
  USING (tenant_id = odn_current_tenant()) WITH CHECK (tenant_id = odn_current_tenant());

REVOKE ALL ON data_definitions, data_definition_history FROM odn_app;
GRANT SELECT, INSERT, UPDATE ON data_definitions TO odn_app;
GRANT SELECT, INSERT ON data_definition_history TO odn_app;
