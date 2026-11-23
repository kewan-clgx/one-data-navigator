-- Allow global connections (tenant_id = NULL) to be accessible by any tenant
-- Global connections are shared across all tenants and need to be readable/writable by any user in any tenant

-- Drop the existing overly-restrictive policy
DROP POLICY tenant_isolation ON connections;

-- Create a new policy that:
-- 1. Allows access to tenant-scoped connections (normal case)
-- 2. Allows access to global connections (tenant_id IS NULL) with owner_user_id IS NULL (shared across all tenants)
CREATE POLICY tenant_isolation ON connections
  USING (
    (tenant_id = odn_current_tenant() AND (owner_user_id IS NULL OR owner_user_id = odn_current_user()))
    OR
    (tenant_id IS NULL AND owner_user_id IS NULL)  -- Global shared connections
  )
  WITH CHECK (
    (tenant_id = odn_current_tenant() AND (owner_user_id IS NULL OR owner_user_id = odn_current_user()))
    OR
    (tenant_id IS NULL AND owner_user_id IS NULL)  -- Global shared connections
  );
