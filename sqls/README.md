# Share BigQuery Public Data Connection

This directory contains one-time SQL scripts for administrative tasks.

## Script: `20261122_create_platform_bq_public_data.sql`

**Purpose:** Create the bigquery-public-data connection as a platform-level resource, making it globally accessible to all tenants and users.

### Background

QueryMuse stores data connections in three scopes:

| Scope | `tenant_id` | `owner_user_id` | Visibility | Use Case |
|---|---|---|---|---|
| **Personal** | User's tenant | User's UUID | Only that user | Private credentials |
| **Shared/Org** | Org's tenant | `NULL` | All users in that tenant | Team data sources |
| **Platform** | `NULL` | `NULL` | All users in all tenants | Public datasets, service accounts |

The bigquery-public-data connection is a **public dataset** with a **service account key** (not personal credentials), so it should be **platform-level** so everyone can use it without per-tenant duplication.

**Prerequisites:**
- Database migration `0023_platform_connections.sql` must be applied first (adds support for `tenant_id = NULL`).
- This migration is automatic when you deploy; no manual action needed.

### How to Run

#### On Production

```bash
# SSH into the production VM or connect to prod database
psql -U odn_app -d odn < sqls/20261122_create_platform_bq_public_data.sql
```

#### On Local Dev

```bash
cd backend/..  # one-data-navigator root
docker-compose exec -T postgres psql -U odn_app -d odn < sqls/20261122_create_platform_bq_public_data.sql
```

### What the Script Does

1. **Checks** for existing platform-level bigquery-public-data connections.
2. **Creates or updates** the connection with:
   - `tenant_id = NULL` (platform-level)
   - `owner_user_id = NULL` (shared)
   - `name = 'bigquery-public-data'`
   - `config` with `project: bigquery-public-data`, `location: US`
   - `secret_ref` pointing to Secret Manager (or update `secret_ciphertext` if storing encrypted locally)
   - `allowed_objects` whitelist: `samples`, `usa_names`, `austin_bikeshare`, `austin_311`, `austin_crime`
3. **Verifies** the connection is accessible via RLS policy.

### Credentials: Secret Manager vs. Encrypted Storage

The script references `secret_ref: 'connection:platform:shared:bq-public-data'` (Secret Manager).

**Option A: Secret Manager (Recommended for prod)**
1. Upload the service account key to Google Cloud Secret Manager in your GCP project.
2. Configure the deployment with:
   - `ODN_SECRETS_KEY=<master-key>`
   - `ODN_SECRETS_PROJECT=<gcp-project>`
3. The backend will fetch the credential at runtime.

**Option B: Encrypted storage (For dev/small scale)**
1. Encrypt the JSON key with the app's master key.
2. Store the ciphertext in `secret_ciphertext` column instead of `secret_ref`:
   ```sql
   UPDATE connections
   SET secret_ciphertext = <encrypted-json>, secret_ref = NULL
   WHERE tenant_id IS NULL AND name = 'bigquery-public-data';
   ```

### After Running

1. **Verify:** Query the connections table:
   ```sql
   SELECT id, name, tenant_id, owner_user_id, config FROM connections
   WHERE tenant_id IS NULL AND kind = 'bigquery';
   ```

2. **Test:** From the QueryMuse frontend:
   - Log in as any user (any tenant).
   - Go to **Settings → Connections**.
   - You should see **bigquery-public-data** in the shared connections list.
   - Query a table: `SELECT * FROM bigquery-public-data.samples.shakespeare LIMIT 10`.

3. **Audit:** Check updated_at timestamp to confirm the script ran.

### Credentials: Where to Get the Key

If you haven't created the service account yet, follow [docs/config/bigquery-public-data-setting.md](../docs/config/bigquery-public-data-setting.md):

```bash
gcloud iam service-accounts create sa-bq-public-reader --project <project>
gcloud projects add-iam-policy-binding <project> \
  --member serviceAccount:sa-bq-public-reader@<project>.iam.gserviceaccount.com \
  --role roles/bigquery.jobUser
gcloud iam service-accounts keys create key.json \
  --iam-account sa-bq-public-reader@<project>.iam.gserviceaccount.com --project <project>
```

Then upload `key.json` to Secret Manager or update the `secret_ciphertext` column.

### Rollback

If you need to delete the platform connection:

```sql
DELETE FROM connections WHERE tenant_id IS NULL AND kind = 'bigquery' AND name = 'bigquery-public-data';
```

This won't affect any user's existing work; RLS ensures users only see the connection (they can't modify it).

### Why Platform-Level?

1. **Singular source of truth:** One service account, one billing project, one allowlist.
2. **Cost tracking:** All queries bill to the same project; unified audit log.
3. **Maintenance:** Update credentials once; everyone gets the new key.
4. **User experience:** No redundant setup or permission management per tenant.

