# Production BigQuery Public Data - Complete Fix Summary

## Issues Fixed

### Issue 1: Credential Decryption Error
**Error:** "connection credentials unavailable: credential cannot be decrypted Dismiss"
**Root Cause:** The global connection was encrypted with incorrect AAD (Additional Authenticated Data)

The connection had been created as tenant-scoped, then promoted to global, but the re-encryption used tenant-id-based AAD instead of global AAD.

**Fix:** Re-encrypted the connection with correct AAD for global scope:
```
AAD: "connection:platform:shared:<connection_id>"
```

### Issue 2: Row-Level Security Violation
**Error:** HTTP 500 when saving connection changes
**Root Cause:** RLS policy on `connections` table required `tenant_id = odn_current_tenant()`, blocking access to global connections where `tenant_id = NULL`

**Fix:** Updated RLS policy to allow global shared connections:
```sql
-- Now allows:
-- 1. Tenant-scoped connections: tenant_id = current_tenant AND (owner_user_id IS NULL OR owner_user_id = current_user)
-- 2. Global shared connections: tenant_id IS NULL AND owner_user_id IS NULL
```
Migration: `0024_global_connections_rls.sql`

### Issue 3: Missing Billing Project Configuration
**Error:** "User does not have bigquery.jobs.create permission in project bigquery-public-data"
**Root Cause:** Connection was configured to run jobs on `bigquery-public-data`, but the service account only has permissions in its own project (`datahop-514819`)

**Solution:** Added `billing_project` configuration:
- **Data project:** `bigquery-public-data` (source of datasets)
- **Billing project:** `datahop-514819` (where queries execute and costs are billed)

## Changes Applied

### 1. Database Encryption Fix
- Generated correct ciphertext with global AAD using the same service account key
- Updated global connection with new ciphertext
- Synchronized tenant connection (via `update_bq_correct_aad.py`)

### 2. Code Changes
- **Migration:** `backend/src/odn/db/migrations/0024_global_connections_rls.sql`
  - Drops overly-restrictive `tenant_isolation` policy
  - Creates new policy that allows access to global shared connections

### 3. Configuration Update
- Added `billing_project: datahop-514819` to connection config
- Now queries execute with proper billing setup

## Verification

✅ **Encryption:** Global connection uses correct AAD
✅ **RLS Policy:** Global connections accessible to all users
✅ **Configuration:** Billing project properly set
✅ **Credentials:** Service account correctly decrypts
✅ **Permissions:** Service account has BigQuery Job User role in its project

## User Impact

**Before:**
- ❌ Users got credential/decryption errors
- ❌ Connection update failed with RLS violation
- ❌ Queries failed with permission denied

**After:**
- ✅ All users can access `bigquery-public-data`
- ✅ Datasets visible in explorer
- ✅ Queries execute successfully
- ✅ Costs billed to correct project

## Deployment Timeline

1. **Credential fix:** Applied directly to production database (no code changes)
2. **Code changes:** Committed to main branch
3. **Migration:** Auto-applied on deployment of commit `0b0bc34`
4. **Configuration:** Applied via database update script
5. **Verification:** All checks pass

## Testing Checklist

- [x] Global connection accessible via RLS
- [x] Credential decryption works (correct AAD)
- [x] Connection test passes all 4 checks:
  - [x] Auth: Service account authenticates
  - [x] Network: Can reach BigQuery API
  - [x] Metadata: Can list datasets in bigquery-public-data
  - [x] Read: Can execute test query with billing project

## Next Steps

If users still experience issues:
1. Clear browser cache (connection config is cached)
2. Re-login to refresh session
3. Try connection test again
4. Check API logs if errors persist:
   ```bash
   deploy/deploy.sh logs api | tail -100
   ```

## References

- RLS policy update: `backend/src/odn/db/migrations/0024_global_connections_rls.sql`
- Configuration docs: `docs/config/bigquery-public-data-setting.md`
- BigQuery connector: `backend/src/odn/platform/connectors/bigquery.py`
