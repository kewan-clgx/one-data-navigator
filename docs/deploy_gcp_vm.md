# Deploying One Data Navigator to a GCP VM

The production environment is a single Compute Engine VM running the same Docker Compose stack as local
development, plus `docker-compose.prod.yml`. There is no CI/CD: you deploy the committed `HEAD` from your
Mac with `deploy/deploy.sh`.

| | Local (Mac) | Prod (`odn-prod` VM, project `datahop-514819`) |
|---|---|---|
| Compose files | `docker-compose.yml` | `docker-compose.yml` + `docker-compose.prod.yml` |
| Entry point | `http://localhost:3100` | `https://odn.datahop.ai` (Caddy, Let's Encrypt) |
| Sign-in | mock OIDC (`oidc` container) | Google (OIDC); `cotality.com` → tenant `cotality`, others → `datahop` |
| Workspace storage | fake GCS, or `gs://datahop` with a key file | `gs://datahop/odn/` via the VM service account (no key file) |
| Warehouse credentials | `file:` key in `./keys` | Secret Manager `odn-<tenant_id>-*` (read by the VM service account) |
| LLM | LiteLLM proxy | Gemini API (AI Studio key) via LiteLLM |
| Postgres | container, local-dev passwords | container on the VM disk, generated passwords, daily disk snapshots |

Only ports 80/443 are public. API, agent, worker and Postgres are reachable only on the internal Docker
network; SSH is allowed only through IAP.

## 1. One-time setup

Prerequisites: `gcloud` authenticated as an owner/editor of `datahop-514819`, and the `default` VPC.

1. **Create the VM and supporting resources**:

   ```sh
   deploy/gcp/create-vm.sh
   ```

   This creates the `odn-vm` service account (Secret Manager accessor, object admin on `gs://datahop`, log
   writer), a static IP, firewall rules (HTTPS public, SSH from IAP only), a 14-day daily snapshot schedule,
   and an `e2-standard-4` Debian 12 VM with Docker installed by its startup script. Defaults are in
   `deploy/gcp/config.sh` and can be overridden through environment variables.

2. **DNS**: add an `A` record `odn.datahop.ai` → the static IP printed by the script. Caddy obtains the TLS
   certificate on first start, so DNS must resolve before step 5.

3. **Google OAuth client** (Console → Google Auth Platform):
   - Audience: **External** (so both personal Google accounts and `cotality.com` accounts can sign in).
     Scopes: `openid`, `email`, `profile`. While the app is in *Testing*, add test users or publish it.
   - Clients → Create → **Web application**. Authorized redirect URI:
     `https://odn.datahop.ai/auth/callback`. Copy the client ID and secret.

4. **Production settings**: copy `deploy/.env.prod.example` to `deploy/.env.prod` (gitignored), fill it
   in, then upload it:

   ```sh
   deploy/deploy.sh env deploy/.env.prod
   ```

   - `ODN_OIDC_DEFAULT_TENANT_EMAILS`: anyone with a Google account can sign in to the `datahop` tenant
     and use the managed LLM budget unless you list allowed emails/`@domains` here.
   - `LITELLM_MODEL`: use a model ID from the Gemini API with the `gemini/` prefix (for example
     `gemini/gemini-3.5-flash`). Names such as `gemini-3.5-flash-genaicenter-us` are corporate-proxy aliases
     and do not exist in the public Gemini API.

5. **First deploy**:

   ```sh
   deploy/deploy.sh up
   ```

6. **Tenants** (signing in fails with "tenant is not provisioned" until they exist):

   ```sh
   deploy/deploy.sh provision tenant datahop --name "DataHop"
   deploy/deploy.sh provision tenant cotality --name "Cotality"
   deploy/deploy.sh provision show        # prints tenant ids
   ```

7. **Enterprise client BigQuery connection** (e.g. tenant `cotality`). The VM service account cannot be
   granted access to a client's project in another organization, so the connection uses the client's
   service-account key stored in Secret Manager in `datahop-514819`. The secret name must start with
   `odn-<tenant id>-`:

   ```sh
   TID=<cotality tenant id from "provision show">
   gcloud --project datahop-514819 secrets create "odn-$TID-bq-idap" --replication-policy automatic
   gcloud --project datahop-514819 secrets versions add "odn-$TID-bq-idap" --data-file keys/<client-sa>.json
   deploy/deploy.sh provision connection cotality idap-int \
     --project clgx-idap-bigquery-int-278e \
     --secret-ref "projects/datahop-514819/secrets/odn-$TID-bq-idap/versions/latest" \
     --allow 'clgx-idap-bigquery-int-278e.<dataset>.*' --default-dataset <dataset>
   ```

   Repeat `--allow` for each approved dataset (`project.dataset.*`) or table (`project.dataset.table`).
   Confirm with the enterprise client that storing their key outside their organization is permitted.

8. **Roles**: users are created on first sign-in as `member`. Promote data leads after they have signed in:

   ```sh
   deploy/deploy.sh provision role cotality someone@cotality.com data_lead
   ```

## 2. Day-to-day

```sh
git commit ...                      # only committed HEAD is deployed
deploy/deploy.sh up                 # ship, rebuild changed images, restart, keep last 5 releases
deploy/deploy.sh ps                 # service status
deploy/deploy.sh logs api agent     # follow logs
deploy/deploy.sh ssh                # shell in the current release directory
deploy/deploy.sh provision budget datahop 500000   # per-tenant daily LLM token limit
```

Settings changes: edit `deploy/.env.prod`, then run `deploy/deploy.sh env deploy/.env.prod` and
`deploy/deploy.sh up`.

Layout on the VM: `/opt/odn/releases/<sha>` (code), `/opt/odn/current` (symlink to the running
release), `/opt/odn/shared/.env` (settings, mode 600). Data lives in Docker volumes (`postgres_data`,
`caddy_data`) on the boot disk, which is snapshotted daily.

## 3. User settings: own LLM keys and data connections

Signed-in users manage these themselves in the app's **Settings** tab; no operator step is needed.

- **AI model**: users add their own Gemini, OpenAI, Anthropic or OpenAI-compatible key, and the chat then runs
  on it at their own cost. Users without a key get a daily trial on the platform key
  (`ODN_LLM_TRIAL_USER_DAILY_TOKENS` per user, inside the tenant's `ODN_LLM_DAILY_TOKEN_BUDGET`). When the trial
  runs out, the chat asks them to add their own key.
- **Data connections**: users add BigQuery connections with a service-account JSON key. These personal
  connections are visible only to their owner. Shared connections created with `deploy.sh provision
  connection` stay visible to the whole tenant.
- Both kinds of key are encrypted in Postgres with `ODN_SECRETS_KEY` (AES-256-GCM, bound to the owner). **Back
  up this value outside the VM.** If it is lost or changed, every saved user key becomes unreadable and users
  must enter their keys again.

## 4. Notes and limits

- **Sessions last up to 1 hour** (the Google ID token lifetime); users sign in again after that. Token
  refresh is still on the hardening list.
- **Database passwords** in `.env` are applied only when Postgres initializes an empty volume. To rotate them
  later, run `ALTER ROLE ... PASSWORD` through `deploy/deploy.sh ssh`, then update `.env`.
- **Shared VM identity**: every container can reach the metadata server, so the worker and agent can also
  act as the VM service account, which can read the bucket and the Secret Manager secrets, including the
  enterprise client keys. The worker runs only policy-checked DuckDB SQL, and the agent reaches data only through the
  API, so this is accepted for the single-VM MVP. To narrow it, grant `secretAccessor` on the individual
  secret instead of the project. The Cloud Run design gives each service its own identity.
- **Restore**: create a disk from a snapshot of the boot disk and recreate the VM from it.
