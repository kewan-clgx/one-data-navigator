-- Platform-level connections: globally available to all tenants and users.
--
-- Background:
-- - Personal connections have owner_user_id set, tenant_id set (visible only to that user in that tenant).
-- - Shared connections have owner_user_id = NULL, tenant_id set (visible to all users in that tenant).
-- - Platform connections have owner_user_id = NULL, tenant_id = NULL (visible to all users in all tenants).
--
-- Platform connections are read-only, system-provisioned resources (e.g., bigquery-public-data).
-- They are managed by administrators via direct SQL or a future admin API.

-- Allow tenant_id to be NULL for platform-level connections.
-- This constraint was created in migration 0001; check if it exists before dropping.
BEGIN;
  ALTER TABLE connections
    DROP CONSTRAINT IF EXISTS connections_tenant_id_name_key;
COMMIT;

ALTER TABLE connections
  ALTER COLUMN tenant_id DROP NOT NULL;

-- Add index for platform connections (tenant_id IS NULL).
CREATE UNIQUE INDEX IF NOT EXISTS connections_platform_name ON connections (name) WHERE tenant_id IS NULL;

-- Update RLS policy to include platform connections (tenant_id IS NULL is visible to all).
DROP POLICY IF EXISTS tenant_isolation ON connections;
CREATE POLICY tenant_isolation ON connections
  USING (
    tenant_id IS NULL  -- Platform connections visible to everyone
    OR (
      tenant_id = odn_current_tenant()
      AND (owner_user_id IS NULL OR owner_user_id = odn_current_user())
    )
  )
  WITH CHECK (
    -- Can only create/update within current tenant (platform connections can only be managed via direct SQL/admin API).
    tenant_id = odn_current_tenant()
    AND (owner_user_id IS NULL OR owner_user_id = odn_current_user())
  );

COMMENT ON COLUMN connections.tenant_id IS 'UUID of the tenant that owns this connection. NULL = platform-level connection visible to all tenants.';

