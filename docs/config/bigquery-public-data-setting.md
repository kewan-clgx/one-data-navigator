# QueryMuse: Access BigQuery Public Data

This guide shows how to configure QueryMuse to query Google Cloud's [`bigquery-public-data`](https://cloud.google.com/bigquery/public-data) datasets. Queries run on your own GCP project and are billed there.

## Overview

- **Datasets:** `bigquery-public-data` is a public GCP project with hundreds of free datasets (GitHub repos, Wikipedia, NYC taxi rides, weather, census data, etc.)
- **Billing:** Query costs are billed to *your* project, not Google's. The first 1 TB of queries per month is free.
- **Service account:** You need a service account in your project with BigQuery permissions. The account does *not* need access to the public datasets—it only runs jobs in your project.

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
     - `roles/bigquery.readSessionUser` (optional but recommended: fast Storage API reads)

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
| Query hangs or times out | Dataset is very large or query is inefficient. | Reduce the allowlist or add `LIMIT 10000` to queries. |
| High costs | Too many large datasets in the allowlist. | Update the allowlist to include only needed datasets. Use `INFORMATION_SCHEMA` to explore before large queries. |
