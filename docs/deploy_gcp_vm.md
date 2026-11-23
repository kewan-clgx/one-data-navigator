# Deploying QueryMuse to a GCP VM

The production environment is a single Compute Engine VM running the same Docker Compose stack as local
development, plus `docker-compose.prod.yml`. There is no CI/CD: you deploy the committed `HEAD` from your
Mac with `deploy/deploy.sh`.

| | Local (Mac) | Prod (`odn-prod` VM, project `datahop-514819`) |
|---|---|---|
| Compose files | `docker-compose.yml` | `docker-compose.yml` + `docker-compose.prod.yml` |
| Entry point | `http://localhost:3100` | `https://app.querymuse.ai` (Caddy, Let's Encrypt); website `https://www.querymuse.ai` (home + `/privacy`); `querymuse.ai` → www |
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

2. **DNS**: add `A` records `app`, `@` (and `www`) for `querymuse.ai` → the static IP printed by the script
   (details in [section 5](#5-moving-to-a-new-domain-querymuseai)). Caddy obtains the TLS
   certificate on first start, so DNS must resolve before step 5.

3. **Google OAuth client** (Console → Google Auth Platform):
   - Audience: **External** (so both personal Google accounts and `cotality.com` accounts can sign in).
     Scopes: `openid`, `email`, `profile`. While the app is in *Testing*, add test users or publish it.
   - Clients → Create → **Web application**. Authorized redirect URI:
     `https://app.querymuse.ai/auth/callback`. Copy the client ID and secret.

4. **Production settings**: copy `deploy/.env.prod.example` to `deploy/.env.prod` (gitignored), fill it
   in, then upload it:

   ```sh
   deploy/deploy.sh env deploy/.env.prod
   ```

   - `ODN_OIDC_DEFAULT_TENANT_EMAILS`: anyone with a Google account can sign in to the issuer's `'*'` tenant
     (prod: `personal`) and use the managed LLM budget unless you list allowed emails/`@domains` here.
   - `ODN_PERSONAL_TENANT`: tenant for email/password personal sign-up (prod: `personal`, created with
     `deploy/deploy.sh provision tenant personal --name Personal --join domain --create-idp-tenant`). Keep it
     free of shared connections: anyone can join it.
   - `LITELLM_MODEL`: use a model ID from the Gemini API with the `gemini/` prefix (for example
     `gemini/gemini-3.5-flash`). Names such as `gemini-3.5-flash-genaicenter-us` are corporate-proxy aliases
     and do not exist in the public Gemini API.

5. **First deploy**:

   ```sh
   deploy/deploy.sh up
   ```

6. **Tenants** (signing in fails with "tenant is not provisioned" until they exist):

   ```sh
   deploy/deploy.sh provision tenant datahop --name "QueryMuse"
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

## 5. Moving to a new domain (querymuse.ai)

The product is **QueryMuse**: the app is `https://app.querymuse.ai`, the website `www.querymuse.ai` (home page
and `/privacy`, served by the same frontend; the apex redirects to it), and the product email is `support@querymuse.ai`. `odn.datahop.ai` is
retired after the cutover (no redirect). The VM, static IP `136.71.108.203`, GCP project and bucket stay
the same, so **clients' database firewall allowlists do not change**.

1. **DNS at the registrar (GoDaddy → querymuse.ai → DNS)**:

   | Type | Name | Value | Notes |
   |---|---|---|---|
   | A | `app` | `136.71.108.203` | the app |
   | A | `@` | `136.71.108.203` | replace GoDaddy's default "Parked"/website A record; turn off Domain Forwarding |
   | CNAME | `www` | `@` (or `app.querymuse.ai`) | GoDaddy usually creates `www → @`; keep it |
   | CAA | `@` | `0 issue "letsencrypt.org"` | optional; only Let's Encrypt may issue certificates |
   | TXT | `_dmarc` | `v=DMARC1; p=none; rua=mailto:support@querymuse.ai` | start with `p=none`, tighten to `quarantine` once mail flows |

   Plus the **mailbox provider's** records for `support@querymuse.ai` (MX, one SPF `TXT @ "v=spf1 include:… ~all"`,
   DKIM), copied from its setup page; and later the **transactional sender's** records (e.g. Resend: MX/SPF on
   `send` and `resend._domainkey`; they don't clash with the mailbox's SPF). Only one `v=spf1` TXT may exist on `@`.
   Check: `dig +short app.querymuse.ai querymuse.ai www.querymuse.ai` all return `136.71.108.203`.

2. **Google OAuth** (Console → Google Auth Platform, project `datahop-514819`):
   - Clients → the web client → add redirect URI `https://app.querymuse.ai/auth/callback` (keep the old one
     until the cutover is verified).
   - Branding: app name **QueryMuse**, home page `https://www.querymuse.ai`, authorized domain `querymuse.ai`,
     developer contact `support@querymuse.ai`. (The user-support email must be a Google account or group you
     own.) A published External app may ask you to verify `querymuse.ai` in Search Console (a DNS TXT record).

3. **Identity Platform** (operator, via API or Console → Identity Platform → Settings): add authorized domain
   `app.querymuse.ai`; email template sender name **QueryMuse**, reply-to `support@querymuse.ai` (once the mailbox
   exists).

4. **Prod settings** in `/opt/odn/.env` (or `deploy/deploy.sh env <file>`):

   ```sh
   ODN_HOSTNAME=app.querymuse.ai
   ODN_SITE_HOSTNAME=www.querymuse.ai   # public home page + /privacy; other paths redirect to the app
   ODN_INSIGHTS_HOSTNAME=insights.querymuse.ai  # public Insight Wall; DNS A record to the VM like the others
   ODN_REDIRECT_HOSTS=querymuse.ai      # -> the website
   ACME_EMAIL=support@querymuse.ai   # optional
   ```

5. **Deploy and rename the staff tenant** (only after step 1 resolves; Caddy then issues the certificates):

   ```sh
   deploy/deploy.sh up
   deploy/deploy.sh provision tenant datahop --name "QueryMuse"
   ```

   Everyone signs in again at the new address (cookies are per host). Re-send pending, unused invites, and tell
   Cotality and Indulge users the new URL.

6. **Branded emails (Resend)**: domain `querymuse.ai` verified in Resend; a sending-only API key stored in
   Secret Manager without passing through chat or shell history:

   ```sh
   read -rs K && printf %s "$K" | gcloud secrets create odn-resend-api-key \
     --project datahop-514819 --replication-policy automatic --data-file=- ; unset K
   ```

   and `ODN_RESEND_API_KEY_SECRET=projects/datahop-514819/secrets/odn-resend-api-key/versions/latest` in the prod
   `.env`. Rotate with `gcloud secrets versions add` and restart the api. Without it, invites use Identity Platform's
   own email.

7. **Retire `odn.datahop.ai`** once sign-in works on the new host: delete its A record at GoDaddy, remove its
   OAuth redirect URI and Identity Platform authorized domain.
