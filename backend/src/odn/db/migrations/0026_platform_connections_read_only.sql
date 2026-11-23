-- Platform connections (tenant_id IS NULL, e.g. bigquery-public-data; migration 0023) are shared by every
-- organization, so no organization may change them: 0024's policy let any tenant's app role update, delete or
-- create them. Now: every organization (with a tenant context) can READ them; writes only reach the caller's own
-- tenant rows. Platform connections are managed by the database owner (direct SQL / provisioning), as 0023 intended.
DROP POLICY IF EXISTS tenant_isolation ON connections;
CREATE POLICY tenant_isolation ON connections
  USING (tenant_id = odn_current_tenant() AND (owner_user_id IS NULL OR owner_user_id = odn_current_user()))
  WITH CHECK (tenant_id = odn_current_tenant() AND (owner_user_id IS NULL OR owner_user_id = odn_current_user()));

-- Read-only access to platform connections, and only inside an organization's context (no context = no rows).
DROP POLICY IF EXISTS platform_read ON connections;
CREATE POLICY platform_read ON connections FOR SELECT
  USING (tenant_id IS NULL AND owner_user_id IS NULL AND odn_current_tenant() IS NOT NULL);
