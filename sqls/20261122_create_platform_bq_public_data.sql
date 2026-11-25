-- Make bigquery-public-data globally available to all tenants and users
--
-- Background:
-- - Personal connections: owner_user_id set, tenant_id set (visible only to that user in that tenant).
-- - Shared connections: owner_user_id = NULL, tenant_id set (visible to all users in that tenant).
-- - Platform connections: owner_user_id = NULL, tenant_id = NULL (visible to all users in all tenants).
--
-- This script creates or updates the bigquery-public-data connection as a PLATFORM-LEVEL connection,
-- making it globally accessible without replication or per-tenant configuration.
--
-- Prerequisites:
-- - Migration 0023_platform_connections.sql must be applied (allows tenant_id = NULL).
-- - Run this script as the database owner (superuser) or use a role with BYPASSRLS.
--   The odn_app role cannot insert with tenant_id=NULL due to RLS policy.
--
-- Running as superuser (or via odn role):
--   psql -U odn -d odn < this_script.sql
--   OR
--   PGPASSWORD=... psql -U odn -h localhost -d odn < this_script.sql

-- If you want to disable RLS for this transaction (alternative approach):
-- ALTER TABLE connections DISABLE ROW LEVEL SECURITY;
-- [INSERT statements here]
-- ALTER TABLE connections ENABLE ROW LEVEL SECURITY;

BEGIN;

-- Step 1: Check if a platform-level bigquery-public-data connection already exists
\echo 'Step 1: Checking for existing platform connections...'
SELECT 
  'Existing platform connections:' AS info,
  id,
  name,
  kind,
  config,
  created_at
FROM connections
WHERE tenant_id IS NULL
  AND kind = 'bigquery'
  AND config::text LIKE '%bigquery-public-data%';

-- Step 2: Create or update the platform-level connection
-- Use ON CONFLICT to upsert (create if not exists, update if exists)
\echo 'Step 2: Creating/updating platform-level connection...'
INSERT INTO connections (
  id,
  tenant_id,
  name,
  kind,
  config,
  secret_ref,
  allowed_objects,
  owner_user_id,
  created_at,
  updated_at
) VALUES (
  gen_random_uuid(),
  NULL,  -- Platform-level: visible to all tenants
  'bigquery-public-data',
  'bigquery',
  jsonb_build_object(
    'project', 'bigquery-public-data',
    'location', 'US'
  ),
  'connection:platform:shared:bq-public-data',  -- Secret Manager reference (configure separately)
  ARRAY[
    'bigquery-public-data.samples.*',
    'bigquery-public-data.usa_names.*',
    'bigquery-public-data.austin_bikeshare.*',
    'bigquery-public-data.austin_311.*',
    'bigquery-public-data.austin_crime.*',
    'bigquery-public-data.thelook_ecommerce.*'
  ],
  NULL,  -- Shared: not owned by any user
  now(),
  now()
)
ON CONFLICT (name) WHERE tenant_id IS NULL DO UPDATE
SET 
  config = EXCLUDED.config,
  secret_ref = EXCLUDED.secret_ref,
  allowed_objects = EXCLUDED.allowed_objects,
  updated_at = now()
WHERE connections.kind = 'bigquery';

-- Step 3: Verify the connection was created/updated
\echo 'Step 3: Verifying connection...'
SELECT 
  'Platform connection ready!' AS info,
  id,
  name,
  kind,
  config,
  secret_ref,
  allowed_objects,
  updated_at
FROM connections
WHERE tenant_id IS NULL
  AND kind = 'bigquery'
  AND config::text LIKE '%bigquery-public-data%';

COMMIT;

-- ===================================================================
-- NEXT STEPS
-- ===================================================================
--
-- 1. The connection is now visible to all tenants via RLS policy.
--    Every `tenant_transaction` will see it because the RLS policy includes:
--    USING (tenant_id IS NULL OR (tenant_id = odn_current_tenant() AND ...))
--
-- 2. If using Secret Manager for credentials:
--    - Create/upload the service account key to Secret Manager.
--    - The secret_ref format is: connection:platform:shared:bq-public-data
--    - Configure ODN_SECRETS_KEY and ODN_SECRETS_PROJECT in deployment.
--
-- 3. If storing credentials encrypted locally:
--    - Update the secret_ciphertext column instead of secret_ref.
--    - Encrypt the JSON key with the app's master key.
--    UPDATE connections
--    SET secret_ciphertext = <encrypted-key-bytes>, secret_ref = NULL
--    WHERE tenant_id IS NULL AND name = 'bigquery-public-data';
--
-- 4. Test: Run a query from multiple tenant/user combinations to verify access.
--
-- 5. UI: QueryMuse will show this connection in all users' connection lists
--    under "Shared" (since owner_user_id IS NULL but it's platform-level).
--
-- ===================================================================


