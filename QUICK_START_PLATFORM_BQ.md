# Quick Start: Global BigQuery Public Data Connection

**Goal:** Make `bigquery-public-data` available to ALL users in ALL tenants.

## What You Get

✓ One platform-level connection visible to every user  
✓ No per-user or per-tenant setup needed  
✓ Automatic via RLS policy (no backend code changes)  
✓ Backward compatible (no breaking changes)  

## Deployment (3 Steps)

### 1. Deploy the migration (automatic)

```bash
# Next deployment will run this migration automatically
docker-compose up  # or your deployment script

# Or manually:
psql -U odn -d odn < backend/src/odn/db/migrations/0023_platform_connections.sql
```

### 2. Create the platform connection (manual, one-time)

```bash
# Local dev:
docker-compose exec -T postgres psql -U odn -d odn < sqls/20261122_create_platform_bq_public_data.sql

# Production:
psql -U odn -d odn < sqls/20261122_create_platform_bq_public_data.sql
```

**Important:** Run as superuser (`odn`), not `odn_app`.

### 3. Configure credentials

**Option A: Secret Manager (Recommended for prod)**
1. Upload the service account key to Google Cloud Secret Manager
2. Configure your deployment:
   ```bash
   export ODN_SECRETS_KEY=<master-key>
   export ODN_SECRETS_PROJECT=<gcp-project-id>
   ```

**Option B: Encrypted local storage**
```sql
UPDATE connections
SET secret_ciphertext = <encrypted-json-bytes>, secret_ref = NULL
WHERE tenant_id IS NULL AND name = 'bigquery-public-data';
```

## Verify

1. Log in as any user (any tenant)
2. Go to **Settings → Connections**
3. See `bigquery-public-data` under **Shared**
4. Query: `SELECT * FROM bigquery-public-data.samples.shakespeare LIMIT 10`

## Files Created

| File | Purpose |
|---|---|
| `backend/src/odn/db/migrations/0023_platform_connections.sql` | Database migration (automatic) |
| `sqls/20261122_create_platform_bq_public_data.sql` | Admin script (run once manually) |
| `sqls/deploy-platform-bq.sh` | Deployment helper |
| `docs/PLATFORM_CONNECTIONS_SETUP.md` | Comprehensive guide |
| `docs/config/bigquery-public-data-setting.md` | User guide |
| `sqls/README.md` | Updated with platform connection info |

## How It Works

```
┌─────────────────────────────────────────────────┐
│ Tenant A                                        │
│  ├─ Personal (user1)                            │
│  ├─ Shared (all users in Tenant A)              │
│  └─ ► Platform (all users, all tenants) NEW     │
└─────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────┐
│ Tenant B                                        │
│  ├─ Personal (user2)                            │
│  ├─ Shared (all users in Tenant B)              │
│  └─ ► Platform (all users, all tenants) NEW     │
└─────────────────────────────────────────────────┘
```

**RLS Policy:** `tenant_id IS NULL` (platform) OR `tenant_id = current_tenant AND ...`

## No Backend Changes

✓ Existing `list_connections()` works as-is  
✓ Existing `get_connection()` works as-is  
✓ Existing API endpoints unchanged  
✓ Existing UI unchanged  

The RLS policy automatically includes platform connections.

## Rollback

Delete the connection:
```sql
DELETE FROM connections WHERE tenant_id IS NULL AND kind = 'bigquery' AND name = 'bigquery-public-data';
```

## Troubleshooting

| Problem | Fix |
|---|---|
| **Script fails "access denied"** | Run as superuser: `psql -U odn` not `odn_app` |
| **Connection doesn't appear** | Wait 30 seconds for cache invalidation, or restart backend |
| **Query fails 403** | Credentials not configured; upload key to Secret Manager or update `secret_ciphertext` |

## References

- [Full setup guide](docs/PLATFORM_CONNECTIONS_SETUP.md)
- [User guide for adding BQ creds](docs/config/bigquery-public-data-setting.md)
- [Admin script details](sqls/README.md)

## Questions?

See `docs/PLATFORM_CONNECTIONS_SETUP.md` for comprehensive documentation.
