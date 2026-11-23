# BigQuery Public Data - Production Fix Complete ✅

**Status:** All issues resolved and deployed to production
**Deployment:** Commit `0b0bc34` 
**Date:** 2026-11-23

## Summary

The `bigquery-public-data` connection is now **fully functional** for all users. Three critical issues were identified and fixed:

1. ✅ **Credential Encryption** - Fixed incorrect AAD (Additional Authenticated Data)
2. ✅ **RLS Policy** - Updated to allow global shared connections
3. ✅ **Configuration** - Added missing billing project

---

## Issues Fixed

### 1. Credential Decryption Error ❌→✅

**Error Seen:** 
```
"connection credentials unavailable: credential cannot be decrypted Dismiss"
```

**Root Cause:**
- Connection was created as tenant-scoped, then promoted to global
- Re-encryption used **incorrect AAD** for global scope
- Global connections need AAD: `"connection:platform:shared:<id>"`
- Tenant-specific AAD would not match

**Solution:**
- Generated new ciphertext with **correct global AAD**
- Updated global connection in production database
- Synchronized tenant connection with same credentials

**Files Modified:**
- Production database only (via SSH script)

---

### 2. Row-Level Security Violation ❌→✅

**Error Seen:**
```
HTTP 500 Internal Server Error
psycopg.errors.InsufficientPrivilege: new row violates row-level security policy for table "connections"
```

**Root Cause:**
- RLS policy on `connections` table required: `tenant_id = odn_current_tenant()`
- Global connections have `tenant_id = NULL`
- NULL ≠ any specific tenant_id, so RLS blocked all access
- Users could not update connection configuration

**Solution:**
- Created migration `0024_global_connections_rls.sql`
- Updated RLS policy to allow global shared connections:
  ```sql
  (tenant_id = odn_current_tenant() AND ...)    -- tenant-scoped
  OR
  (tenant_id IS NULL AND owner_user_id IS NULL) -- global shared
  ```

**Files Modified:**
- `backend/src/odn/db/migrations/0024_global_connections_rls.sql` (new)
- Deployed via commit `0b0bc34`

---

### 3. BigQuery Permission Error ❌→✅

**Error Seen:**
```
POST https://bigquery.googleapis.com/bigquery/v2/projects/bigquery-public-data/jobs:
Access Denied: Project bigquery-public-data: User does not have bigquery.jobs.create 
permission in project bigquery-public-data
```

**Root Cause:**
- Service account has permissions in its **own project** (`datahop-514819`)
- Connection was configured to run jobs on **public datasets project** (`bigquery-public-data`)
- Service account has no permissions on the public project
- Missing `billing_project` configuration

**How BigQuery Works:**
```
Data location:  bigquery-public-data (public, everyone can read)
Query execution: datahop-514819 (private, our project, our SA has permissions)
Billing:        datahop-514819 (costs billed to our project)
```

**Solution:**
- Added `billing_project: datahop-514819` to connection config
- Queries now execute in service account's own project
- Service account has required BigQuery roles there:
  - `roles/bigquery.jobUser` ✅
  - `roles/bigquery.readSessionUser` ✅

**Configuration Before:**
```json
{
  "project": "bigquery-public-data",
  "location": "US"
}
```

**Configuration After:**
```json
{
  "project": "bigquery-public-data",
  "location": "US",
  "billing_project": "datahop-514819"
}
```

**Files Modified:**
- Production database only (via update script)

---

## Deployment Timeline

| Step | Action | Status |
|------|--------|--------|
| 1 | Fix credential encryption | ✅ Applied to prod DB |
| 2 | Create RLS migration | ✅ Committed & pushed |
| 3 | Deploy code to production | ✅ `deploy/deploy.sh up` ran |
| 4 | Run database migrations | ✅ Migration 0024 applied |
| 5 | Add billing_project config | ✅ Updated in prod DB |
| 6 | Verify all checks pass | ✅ All 4 connection tests pass |

---

## Verification Checklist

- [x] Global connection uses correct encryption AAD
- [x] RLS policy allows global shared connections
- [x] Both global and tenant connections have same credentials
- [x] Billing project configured: `datahop-514819`
- [x] Service account has required BigQuery roles
- [x] Connection test passes all 4 checks:
  - [x] Auth: Service account authenticates ✅
  - [x] Network: Can reach BigQuery API ✅
  - [x] Metadata: Can list datasets in bigquery-public-data ✅
  - [x] Read: Can execute test query with billing project ✅

---

## User Experience

### Before Fix
```
❌ chun.wan@hotmail.com
   Error: credential cannot be decrypted
   
❌ kewan@cotality.com
   Error: credential cannot be decrypted
   
❌ kevinwan@indulgetheapp.com
   Error: User does not have bigquery.jobs.create permission
```

### After Fix
```
✅ chun.wan@hotmail.com
   - Can access bigquery-public-data
   - Can explore datasets
   - Can run queries
   
✅ kewan@cotality.com
   - Can access bigquery-public-data
   - Can explore datasets
   - Can run queries
   
✅ kevinwan@indulgetheapp.com
   - Can access bigquery-public-data
   - Can explore datasets
   - Can run queries
   - Costs billed to datahop-514819
```

---

## Technical Details

### Encryption AAD

The connection credentials are encrypted using ChaCha20Poly1305 with AAD (Additional Authenticated Data) that binds the secret to its context:

**Global Connection AAD:**
```python
"connection:platform:shared:4c178ecf-5148-449c-8b0e-b0611533e24b"
```

**Tenant Connection AAD (before sync):**
```python
"connection:45b33da9-a3dc-4995-bb4b-824734c6ba55:fea74956-6714-4a2e-9379-b76c1fa2a607:6ceed661-1322-4d82-a795-963319ebfb3a"
```

**Tenant Connection AAD (after sync):**
```python
# Same ciphertext as global, decrypts with same AAD
"connection:platform:shared:4c178ecf-5148-449c-8b0e-b0611533e24b"
```

### RLS Policy

Updated `tenant_isolation` policy on `connections` table:

```sql
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
```

This allows:
- **Tenant-scoped connections:** Accessible only by users in that tenant
- **Global shared connections:** Accessible by all users in any tenant

---

## Configuration Files

### Global Connection
- **ID:** `4c178ecf-5148-449c-8b0e-b0611533e24b`
- **Tenant ID:** NULL (global)
- **Owner User ID:** NULL (shared)
- **Config:**
  ```json
  {
    "project": "bigquery-public-data",
    "location": "US",
    "billing_project": "datahop-514819"
  }
  ```

### Tenant Connection (Indulge)
- **ID:** `6ceed661-1322-4d82-a795-963319ebfb3a`
- **Tenant ID:** `45b33da9-a3dc-4995-bb4b-824734c6ba55`
- **Owner User ID:** `fea74956-6714-4a2e-9379-b76c1fa2a607`
- **Config:**
  ```json
  {
    "project": "bigquery-public-data",
    "location": "US",
    "billing_project": "datahop-514819"
  }
  ```

---

## Troubleshooting

If users still experience errors after this fix:

### 1. Browser Cache Issue
```bash
# Clear browser cache and cookies, hard refresh
# Cmd+Shift+R (Mac) or Ctrl+Shift+R (Windows/Linux)
# Or use Incognito/Private mode
```

### 2. Session Not Updated
```bash
# Logout and login again
# New session will load updated connection config from database
```

### 3. Check API Logs
```bash
deploy/deploy.sh logs api | grep -i error
# Look for specific error messages
```

### 4. Verify Database State
```bash
# Connect to production database
psql $ODN_DATABASE_URL

# Check global connection
SELECT config FROM connections 
WHERE id = '4c178ecf-5148-449c-8b0e-b0611533e24b';

# Should show:
# {"project": "bigquery-public-data", "location": "US", "billing_project": "datahop-514819"}
```

---

## Documentation

### For Users
- [BigQuery Public Data Setup Guide](./docs/config/bigquery-public-data-setting.md)
  - Explains why billing project is needed
  - Shows how to set up service account
  - Lists troubleshooting steps

### For Developers
- [RLS Policy Migration](./backend/src/odn/db/migrations/0024_global_connections_rls.sql)
  - See how global connections are allowed by RLS
  - Pattern for other global resources

### For Operations
- [Production Fix Details](./PRODUCTION_BIGQUERY_FIX.md)
  - Complete breakdown of all three issues
  - Database update scripts used
  - Verification procedures

---

## References

**BigQuery Documentation:**
- [Billing Projects](https://cloud.google.com/bigquery/docs/billing-overview#billing-projects)
- [Public Datasets](https://cloud.google.com/bigquery/public-data)
- [IAM Roles](https://cloud.google.com/bigquery/docs/access-control)

**Code:**
- Connector: `backend/src/odn/platform/connectors/bigquery.py`
- Service: `backend/src/odn/platform/connections/service.py`
- Encryption: `backend/src/odn/platform/crypto.py`

**Commits:**
- `0b0bc34` - Fix RLS policy to allow global BigQuery connections

---

## Sign-Off

✅ **All issues resolved**
✅ **Deployed to production**
✅ **Verified and tested**
✅ **Documentation updated**

**Next Steps:** Monitor for any user-reported issues and check logs if problems arise.
