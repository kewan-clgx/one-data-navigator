# QueryMuse: Access BigQuery Public Data

This guide shows how to configure QueryMuse to query Google Cloud's [`bigquery-public-data`](https://cloud.google.com/bigquery/public-data) datasets. Queries run on your own GCP project and are billed there.

## Overview

- **Datasets:** `bigquery-public-data` is a public GCP project with hundreds of free datasets (GitHub repos, Wikipedia, NYC taxi rides, weather, census data, etc.)
- **Billing:** Query costs are billed to *your* project, not Google's. The first 1 TB of queries per month is free.
- **Service account:** You need a service account in your project with BigQuery permissions. The account does *not* need access to the public datasets—it only runs jobs in your project.
- **Global Access (QueryMuse Pro):** Organizations can enable a **shared, globally-accessible connection** to `bigquery-public-data` so all users can query public datasets without individual setup. See [Global Public Access Setup](#global-public-access-setup-organizations) below.

## Quick Setup (Using gcloud CLI)

If you have [`gcloud` installed](https://cloud.google.com/cli/docs/install), run:

```bash
# Replace PROJECT with your GCP project ID (e.g., my-analytics-project)
PROJECT=my-analytics-project
SA=sa-bq-public-reader

gcloud services enable bigquery.googleapis.com bigquerystorage.googleapis.com --project $PROJECT

gcloud iam service-accounts create $SA --project $PROJECT \
  --display-name "QueryMuse bigquery-public-data reader"

EMAIL=$SA@$PROJECT.iam.gserviceaccount.com
gcloud projects add-iam-policy-binding $PROJECT \
  --member serviceAccount:$EMAIL --role roles/bigquery.jobUser
gcloud projects add-iam-policy-binding $PROJECT \
  --member serviceAccount:$EMAIL --role roles/bigquery.readSessionUser

mkdir -p ~/.config/querymuse && chmod 700 ~/.config/querymuse
gcloud iam service-accounts keys create ~/.config/querymuse/$SA-key.json \
  --iam-account $EMAIL --project $PROJECT
chmod 600 ~/.config/querymuse/$SA-key.json
```

Then skip to [Add Connection in QueryMuse](#add-connection-in-querymuse).

## Manual Setup (via Google Cloud Console)

1. **Create a service account:**
   - Go to [Service Accounts](https://console.cloud.google.com/iam-admin/serviceaccounts) in your project.
   - Click **Create Service Account**.
   - Name: `bq-public-reader` (or your choice).
   - Click **Create and Continue**.
   - Skip **Grant this service account access to project** (you'll add roles in the next step).

2. **Grant BigQuery roles:**
   - On the service account detail page, click the **Permissions** tab.
   - Click **Grant Access**.
   - In the sidebar, search for the service account and add:
     - `roles/bigquery.jobUser` (required: submit jobs in your project)
     - `roles/bigquery.readSessionUser` (required: query results are read through the BigQuery Storage Read API)

3. **Create a JSON key:**
   - On the service account detail page, click the **Keys** tab.
   - Click **Add Key** → **Create new key** → **JSON**.
   - Save the JSON file somewhere safe (e.g., `~/.config/querymuse/bq-public-reader-key.json`).
   - **Keep this file private.** Do not commit it to version control.

4. **Enable APIs** (if not already enabled):
   - Go to [APIs & Services](https://console.cloud.google.com/apis/dashboard) in your project.
   - Search for and enable:
     - **BigQuery API**
     - **BigQuery Storage Read API**

## Add Connection in QueryMuse

1. Open QueryMuse and go to **Settings** → **Connections** → **Add connection** → **BigQuery**.

2. Fill in:

   | Field | Value | Notes |
   |---|---|---|
   | **Connection name** | `public-data` or similar | For your reference |
   | **Service-account key (JSON)** | Upload the JSON key file | The file you created/saved above |
   | **Project ID** | `bigquery-public-data` | Hardcoded; queries read from this project |
   | **Billing project** | *Your project ID* | Required; jobs run here and costs bill here |
   | **Location** (optional) | `US` | Most public datasets are in the US multi-region |
   | **Default dataset** (optional) | e.g., `samples` | Shorthand for queries (optional) |
   | **Datasets/tables you can query** (optional) | One per line, e.g.: <br> `samples` <br> `usa_names` <br> `austin_bikeshare` | Allowlist to reduce latency and noise. Leave empty to allow all. See [Allowlist Format](#allowlist-format) below. |

3. Click **Test connection** to verify everything works.

4. Click **Add connection**.

## Allowlist Format

The optional **Datasets/tables** field restricts what the agent can see. If empty, it defaults to all datasets. Use one entry per line:

| Format | Becomes | Scope |
|---|---|---|
| `samples` | `bigquery-public-data.samples.*` | All tables in `samples` dataset |
| `samples.shakespeare` | `bigquery-public-data.samples.shakespeare` | Specific table |
| `github_repos` | `bigquery-public-data.github_repos.*` | All tables in `github_repos` |
| `github_repos.commits` | `bigquery-public-data.github_repos.commits` | Specific table |

**Recommendation:** Allowlist only datasets you plan to use. Many public datasets are very large (e.g., `crypto_bitcoin`, `github_repos`), which can increase query costs and agent latency.

## Why the Billing Project is Required

The connection settings use `project: bigquery-public-data` and `billing_project: <your-project>`. When you submit a query:

1. Tables are read from `bigquery-public-data` (the public dataset).
2. The query job runs in `billing_project` (your project).
3. Costs are billed to your project.

If you omit the billing project, the connection tries to submit jobs to `bigquery-public-data` itself, and your service account (which has no access there) will get a 403 error.

## Cost Management

- **First 1 TB/month:** Free for new GCP accounts and free tier users.
- **Beyond 1 TB:** ~$6.25 USD per TB of data scanned.
- **Ways to control costs:**
  - Use the allowlist to prevent the agent from scanning large, unrelated datasets.
  - Write efficient queries (use column selection, partition filters, etc.).
  - Use `INFORMATION_SCHEMA` queries to explore table schemas before large scans.

See [BigQuery pricing](https://cloud.google.com/bigquery/pricing) for details.

## Testing the Connection

After adding the connection, the **Test connection** button runs these checks:

1. **Auth:** Can the key authenticate with Google?
2. **Network:** Can we reach BigQuery?
3. **Metadata:** Can we list datasets in `bigquery-public-data`?
4. **Read:** Can we run a query (`SELECT 1`)?

All should show ✓. If any fail, check:
- The key is valid and hasn't been revoked.
- The service account has the two BigQuery roles.
- Your billing project ID is correct.

## Cleanup

To delete the service account and key:

```bash
gcloud iam service-accounts delete sa-bq-public-reader@PROJECT.iam.gserviceaccount.com --project PROJECT
```

Then remove the key file from your filesystem.

## Public Datasets

Popular `bigquery-public-data` datasets include:

- **`samples`**: Tutorial tables (Shakespeare, USA names).
- **`github_repos`**: Public GitHub repositories (very large).
- **`austin_311`, `austin_bikeshare`, `austin_crime`**: Austin city data.
- **`thelook_ecommerce`**: Fictional e-commerce store (orders, order_items, products, users, events).
- **`wikipedia`**: Wikipedia page views and metadata.
- **`covid19_open_data`**: COVID-19 cases, deaths, vaccinations.
- **`wikipedia_trends`**: Trending Wikipedia pages.
- **`crypto_bitcoin`, `crypto_ethereum`**: Blockchain data (very large).
- **`noaa_gsod`**: Global weather data.
- **`sec_financial_statements`**: SEC 10-K, 10-Q filings.

Browse all datasets [here](https://console.cloud.google.com/bigquery?project=bigquery-public-data&p=bigquery-public-data) or in the QueryMuse **Catalog** after connecting.

## Troubleshooting

| Problem | Cause | Fix |
|---|---|---|
| Test connection fails at **Auth** | Key is invalid, deleted, or rotated. | Re-download the key from the Google Cloud Console. |
| Test connection fails at **Metadata** | Service account has wrong roles. | Re-check the IAM policy. Add `roles/bigquery.jobUser` and `roles/bigquery.readSessionUser`. |
| Test connection fails at **Read** | Billing project is wrong or key is missing `bigquery.jobUser`. | Verify the billing project ID and re-check roles. |
| `User does not have bigquery.jobs.create permission in project <billing project>` | The key's service account can't run jobs in the billing project (a connection's own key is always used, never the platform identity). | Grant `roles/bigquery.jobUser` on the billing project to that service account. No restart needed. |
| `the user does not have 'bigquery.readsessions.create' permission for 'projects/<billing project>'` | Jobs run, but results are read through the Storage Read API, which needs its own role. | Grant `roles/bigquery.readSessionUser` on the billing project to that service account. |
| Query hangs or times out | Dataset is very large or query is inefficient. | Reduce the allowlist or add `LIMIT 10000` to queries. |
| High costs | Too many large datasets in the allowlist. | Update the allowlist to include only needed datasets. Use `INFORMATION_SCHEMA` to explore before large queries. |

---

## Global Public Access Setup (Organizations)

QueryMuse Pro organizations can enable a **shared `bigquery-public-data` connection** so all users across all tenants can query public datasets without individual setup.

### How It Works

Instead of each user creating their own personal connection, the organization:

1. Creates **one service account** with BigQuery permissions in a GCP project
2. Uploads the service account key **once** to QueryMuse
3. Creates a **global shared connection** visible to all users
4. All users can immediately access `bigquery-public-data` datasets

**Benefits:**
- ✅ Centralized credential management (one key, not many)
- ✅ No per-user setup required
- ✅ All costs tracked in one billing project
- ✅ Consistent configuration across the organization
- ✅ Users across **all tenants** (personal and enterprise) can access

### Implementation for Admins

#### Prerequisites

You need a GCP project where the organization has permissions:

```bash
PROJECT=your-gcp-project          # e.g., my-analytics-project
SA_NAME=sa-bq-public-reader       # or your choice
SA_EMAIL=$SA_NAME@$PROJECT.iam.gserviceaccount.com
```

#### Step 1: Enable APIs

```bash
gcloud services enable bigquery.googleapis.com bigquerystorage.googleapis.com --project $PROJECT
```

#### Step 2: Create Service Account

```bash
gcloud iam service-accounts create $SA_NAME --project $PROJECT \
  --display-name "QueryMuse global BigQuery public data access"
```

#### Step 3: Grant BigQuery Roles

```bash
gcloud projects add-iam-policy-binding $PROJECT \
  --member serviceAccount:$SA_EMAIL --role roles/bigquery.jobUser

gcloud projects add-iam-policy-binding $PROJECT \
  --member serviceAccount:$SA_EMAIL --role roles/bigquery.readSessionUser
```

#### Step 4: Create and Download JSON Key

```bash
gcloud iam service-accounts keys create sa-key.json \
  --iam-account $SA_EMAIL --project $PROJECT
```

**Keep this key file secure and do not commit it to version control.**

#### Step 5: Create Shared Connection in QueryMuse

1. Login as an **organization admin** or **data lead** (requires appropriate role)
2. Go to **Settings** → **Connections** → **Create Connection** → **BigQuery**
3. Fill in:

   | Field | Value |
   |---|---|
   | **Connection name** | `bigquery-public-data` or similar |
   | **Service-account key (JSON)** | Upload the JSON key from step 4 |
   | **Project ID** | `bigquery-public-data` |
   | **Billing project** | Your GCP project ID (e.g., `my-analytics-project`) |
   | **Location** (optional) | `US` (most public datasets are in US multi-region) |
   | **Default dataset** (optional) | Leave empty or specify (e.g., `samples`) |
   | **Datasets/tables** (optional) | Leave empty to allow all, or allowlist specific datasets |

4. Click **Test connection** to verify all 4 checks pass:
   - ✅ **Auth:** Service account authenticates
   - ✅ **Network:** Can reach BigQuery
   - ✅ **Metadata:** Can list datasets in bigquery-public-data
   - ✅ **Read:** Can execute test query in billing project

5. Click **Save** to make the connection available to **all users**

#### Step 6: Verify Global Access

The connection is now automatically visible to all users. Users can:
- Access the connection in the connection explorer
- See datasets in the catalog
- Run queries immediately without any additional setup

### Database Schema (Global Connections)

Internally, global shared connections have:

```sql
-- Global connection visible to all users
SELECT * FROM connections WHERE 
  tenant_id IS NULL              -- No tenant restriction
  AND owner_user_id IS NULL      -- Shared (not personal)
  AND kind = 'bigquery'
  AND name = 'bigquery-public-data';
```

This allows:
- **Any user in any tenant** to access the connection (via RLS policy)
- **No individual credentials needed** (centrally managed)
- **Consistent configuration** across all users

### Cost Management

When using a global connection:

- **Who pays:** Costs are billed to the GCP project specified in the **Billing project** field
- **Who controls costs:** The organization admin
- **How to track:** 
  - Check BigQuery costs in the specified GCP project
  - Optionally restrict datasets via the **Datasets/tables** allowlist

### Updating the Global Connection

If you need to update the connection (e.g., change billing project, add dataset allowlist):

1. Login as an **organization admin** or **data lead**
2. Go to **Settings** → **Connections**
3. Find `bigquery-public-data` and click **Edit**
4. Make your changes
5. Click **Test connection** to verify
6. Click **Save**

**Changes take effect immediately** for all users.

### Troubleshooting Global Access

| Problem | Cause | Fix |
|---|---|---|
| Connection not visible to users | Connection is personal, not global | Make sure `owner_user_id = NULL` and `tenant_id = NULL` in database |
| "HTTP 500" error when updating | RLS policy blocks global connections | Update RLS policy (see [RLS Policy Migration](../db/migrations/0024_global_connections_rls.sql)) |
| "credential cannot be decrypted" | Incorrect encryption AAD | Ensure connection uses global AAD: `connection:platform:shared:<id>` |
| Some users can't access | Connection scoped to specific tenant | Verify connection has `tenant_id = NULL` |

### Deprecation: Personal Connections

Users with personal `bigquery-public-data` connections can keep using them. However, we recommend transitioning to the global connection:

**Benefits of switching to global:**
- ✅ No duplicate credentials to manage
- ✅ Consistent configuration
- ✅ Centralized cost tracking
- ✅ Easier for teams to collaborate

**To migrate:** Delete personal connection (after global connection is set up) and start using the global one.

---

## Troubleshooting (Original - Personal Setup)
