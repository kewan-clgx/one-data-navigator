# Platform-Level BigQuery Public Data Connection

This document summarizes the changes to make bigquery-public-data globally accessible to all tenants and users.

## Summary of Changes

### 1. Database Migration: `0023_platform_connections.sql`

**What:** Adds support for platform-level connections by allowing `tenant_id = NULL`.

**Changes:**
- Alters `connections.tenant_id` from `NOT NULL` to nullable.
- Updates RLS policy to include `tenant_id IS NULL` (platform connections visible to all).
- Adds unique index for platform connections: `connections_platform_name`.
- Adds comment explaining the NULL semantics.

**Impact:** No breaking changes; existing tenant-scoped connections unaffected. The RLS policy is strictly MORE permissive (adds a clause for platform connections).

**Automatic deployment:** This migration runs automatically on the next `docker-compose up` or deployment.

### 2. Admin Script: `sqls/20261122_create_platform_bq_public_data.sql`

**What:** Creates or updates the bigquery-public-data connection as a platform-level resource.

**What it does:**
- Checks for existing platform connections.
- Inserts a new platform connection (or updates if exists) with:
  - `tenant_id = NULL` (global scope)
  - `owner_user_id = NULL` (shared/unowned)
  - `name = 'bigquery-public-data'`
  - `config: { project: "bigquery-public-data", location: "US" }`
  - `secret_ref = "connection:platform:shared:bq-public-data"` (Secret Manager reference)
  - `allowed_objects`: `samples`, `usa_names`, `austin_bikeshare`, `austin_311`, `austin_crime`, `thelook_ecommerce`

**How to run (after migration 0023 is applied):**

```bash
# Production
psql -U odn_app -d odn < sqls/20261122_create_platform_bq_public_data.sql

# Local dev
docker-compose exec -T postgres psql -U odn_app -d odn < sqls/20261122_create_platform_bq_public_data.sql
```

### 3. Backend Service Layer

**What:** No code changes needed!

**Why:** The existing `list_connections(tenant_id, user_id)` and `get_connection(...)` functions use `tenant_transaction`, which applies the updated RLS policy. The RLS policy now includes platform connections (`tenant_id IS NULL`), so these functions automatically return both tenant-scoped AND platform connections.

**Flow:**
1. User requests connections: `GET /v1/connections`
2. API calls `list_connections(ctx.tenant_id, ctx.user_id)`
3. Backend opens `tenant_transaction(ctx.tenant_id, ctx.user_id)`
4. Transaction sets `odn.tenant_id` context variable
5. RLS policy filters: `tenant_id IS NULL OR (tenant_id = odn_current_tenant() AND ...)`
6. Query result includes both platform connections and tenant-scoped connections

## Architecture

```
┌─ All Tenants ─────────────────────────────────────────────────────┐
│                                                                     │
│  ┌─ Tenant A ───────────────────┐  ┌─ Tenant B ─────────────────┐ │
│  │ Personal (user1@A)           │  │ Personal (user2@B)         │ │
│  │ Shared (team data)           │  │ Shared (team data)         │ │
│  │                              │  │                            │ │
│  │  ┌────────────────────────┐  │  │  ┌────────────────────────┐ │
│  │  │ Platform (all tenants) │◄─┼──┼─►│ Platform (all tenants) │ │
│  │  │  - bigquery-public-data│  │  │  │  - bigquery-public-data│ │
│  │  └────────────────────────┘  │  │  └────────────────────────┘ │
│  │                              │  │                            │ │
│  └──────────────────────────────┘  └────────────────────────────┘ │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

**Connection Types:**
1. **Personal:** Visible only to owner within their tenant.
2. **Shared/Org:** Visible to all users in the same tenant.
3. **Platform:** Visible to all users in all tenants (new).

## Deployment Steps

### On Production

1. **Deploy migration** (automatic with app update):
   ```bash
   # Triggered automatically during deployment
   docker-compose up  # or deployment script
   ```

2. **Apply admin script** (manual, after migration):
   ```bash
   ssh your-vm
   psql -U odn_app -d odn < /path/to/sqls/20261122_create_platform_bq_public_data.sql
   ```

3. **Configure credentials** (before or after script):
   - **Secret Manager:** Upload key to GCP Secret Manager; configure `ODN_SECRETS_KEY` and `ODN_SECRETS_PROJECT`.
   - **Encrypted local storage:** Update `secret_ciphertext` column manually.

4. **Verify:**
   ```bash
   # Check connection exists and is accessible
   psql -U odn_app -d odn
   SELECT id, name, tenant_id, owner_user_id, config FROM connections
   WHERE tenant_id IS NULL AND kind = 'bigquery';
   ```

### On Local Dev

1. **Migration runs automatically:** `docker-compose up` triggers migrations.

2. **Apply admin script:**
   ```bash
   cd one-data-navigator
   docker-compose exec -T postgres psql -U odn_app -d odn < sqls/20261122_create_platform_bq_public_data.sql
   ```

3. **Test:** Log in as any user, go to Settings → Connections, see `bigquery-public-data` in shared list.

## User Experience

**Before:** Users had to create their own personal connection to bigquery-public-data (or someone created a tenant-specific one).

**After:** Every user automatically sees `bigquery-public-data` in their connections list, shared/organization-managed scope.

**In the UI:**
- **Settings → Connections** shows:
  - Personal connections (user-owned)
  - Shared connections (org-owned, includes platform connections)
- **Agent can query:** `SELECT * FROM bigquery-public-data.samples.shakespeare`
- **Cost:** Billed to the service account's project (datahop-514819).

## Credential Management

The script uses `secret_ref` for Secret Manager, but you can also store encrypted credentials locally.

**Secret Manager path:**
```
connection:platform:shared:bq-public-data
```

The backend expects this format when fetching from Secret Manager. Configure:
```bash
export ODN_SECRETS_KEY=<master-key>              # For encryption/decryption
export ODN_SECRETS_PROJECT=<gcp-project-id>     # Where Secret Manager lives
```

## Rollback

If needed, delete the platform connection:
```sql
DELETE FROM connections WHERE tenant_id IS NULL AND kind = 'bigquery' AND name = 'bigquery-public-data';
```

This won't break anything; queries will simply fail to list this connection. Users' queries won't be affected (RLS ensures they only see what's returned).

**Rollback migration 0023 (not recommended, complex):**
- Would require re-applying migration 0007 (re-add NOT NULL constraint).
- Not necessary unless you want to remove platform-connection support entirely.

## Testing Checklist

- [ ] Migration 0023 applied successfully (check migration history).
- [ ] Admin script ran without errors.
- [ ] Platform connection appears in `SELECT` from connections table.
- [ ] User in Tenant A logs in, sees bigquery-public-data connection.
- [ ] User in Tenant B logs in, sees the same bigquery-public-data connection.
- [ ] Private user (no tenant) logs in, sees bigquery-public-data connection.
- [ ] Query `SELECT 1 FROM bigquery-public-data.samples.shakespeare LIMIT 1` works.
- [ ] Query costs appear in the service account's GCP project, not user's.
