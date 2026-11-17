# Deploy to GCP: Session Notes (2026-11-16)

These notes record every step taken on 2026-11-16 to put the One Data Navigator MVP live at
**https://odn.datahop.ai**. For the reusable runbook, see [deploy_gcp_vm.md](deploy_gcp_vm.md). This file
records what was decided, what was built, and what happened during the first deploy, including the
problems found and how they were fixed.

No secrets are recorded here. They live only in `deploy/.env.prod` (gitignored, mode 600), on the VM at
`/opt/odn/shared/.env`, and in Secret Manager.

## 0. Final state

| Item | Value |
|---|---|
| URL | https://odn.datahop.ai (Let's Encrypt TLS via Caddy) |
| GCP project | `datahop-514819` |
| VM | `odn-prod`, `us-central1-a`, `e2-standard-4` (4 vCPU / 16 GB), Debian 12, 50 GB `pd-balanced` |
| Static IP | `136.71.108.203` (`odn-prod-ip`) |
| VM service account | `odn-vm@datahop-514819.iam.gserviceaccount.com` |
| Containers | `caddy`, `frontend` (Next.js prod build), `api`, `agent`, `worker`, `postgres` (pgvector 16) |
| Sign-in | Google OIDC; `cotality.com` → tenant `cotality`; allowlisted personal accounts → tenant `datahop` |
| Tenants | `datahop` (`9308761a-85b1-4e36-a42b-b2a146946ac0`), `cotality` (`0a86ccd8-8d9b-45db-917e-9520f63ec989`) |
| Data | Cotality BigQuery `clgx-idap-bigquery-int-278e` (connection `idap-int`, 25 datasets + 2 tables) |
| Workspace storage | `gs://datahop/odn/` via the VM service account |
| LLM | Gemini API (AI Studio key) via LiteLLM, model `gemini/gemini-3.5-flash` |
| Deploys | Manual from the Mac: `deploy/deploy.sh up` (ships committed `HEAD`) |

Verified end-to-end: sign-in with a personal Google account and with a `cotality.com` account, browsing
Cotality datasets, running queries and chatting with the agent.

## 1. Starting point and earlier commits

The day began with Phase 1 and Phase 2 features running locally in Docker Compose. Two commits finished the
durable chat sessions work:

| Commit | Change |
|---|---|
| `f2c82a3` | Durable ADK chat sessions in Postgres (`DatabaseSessionService`, `odn_agent` role) |
| `46ad12a` | Migration `0006_agent_sessions.sql`: `adk` schema, `PUBLIC` access revoked, grants to `odn_agent` |

A review of the [MVP dev plan](one_data_navigator_mvp_dev_plan.md) showed that hosting (the Phase 1
blocker) was waiting on a deploy-target decision between Jenkins + Helm on GKE and Cloud Run + Terraform.

## 2. Deployment decision

Requirements: deploy to a single cloud VM that runs the same Dockerized services as the local Docker
Desktop stack, with a simple, direct deploy process and no CI/CD for now. There is one local Mac
environment and one cloud prod environment. Both GCP and AWS were available.

**Decision: one GCP Compute Engine VM running Docker Compose.** The reasons:

- BigQuery and `gs://datahop` are on GCP. On AWS every query result and Parquet read would leave GCP,
  adding egress fees (about $0.12/GB) and latency.
- On GCP the VM's own service account can access the bucket, so no key files are needed for it.
- Cost is similar (`e2-standard-4` at about $100/month vs `t3.xlarge` at about $120/month plus egress), and GCP
  leaves a direct upgrade path to Cloud SQL, Secret Manager and Cloud Run later.
- 8 GB of RAM is too small, because the DuckDB worker alone is capped at 2 GB.

Other decisions made during the session:

| Topic | Decision | Reason |
|---|---|---|
| Identity provider | Google sign-in | Supports both personal accounts and corporate Google Workspace accounts |
| Tenancy | `hd` claim `cotality.com` → `cotality`; everyone else → `datahop`, limited by an email allowlist | Corporate users see corporate data; app users get their own tenant |
| Domain | `odn.datahop.ai` | Domain owned by the user |
| LLM | Gemini API (AI Studio key) | The corporate LiteLLM proxy (`llm-uat.cotality.com`) needs the corporate network or VPN |
| `gs://datahop` credentials | VM service account | Same project, so no key file is needed |
| BigQuery credentials | Cotality service-account key in Secret Manager | The Cotality project belongs to a different organization, so the VM service account cannot be granted access |

## 3. Code and configuration built (commit `ffb97c8`)

### Backend

- **Google identity → tenant mapping** (`backend/src/odn/tenancy/oidc.py`, `config.py`):
  - `ODN_OIDC_TENANT_DOMAINS` (e.g. `cotality.com=cotality`) maps the claim named by
    `ODN_OIDC_TENANT_CLAIM` (`hd` in prod) to a tenant slug. The match is case-insensitive.
  - `ODN_OIDC_DEFAULT_TENANT` takes identities whose claim is missing or unmapped.
  - `ODN_OIDC_DEFAULT_TENANT_EMAILS` limits the default tenant to listed emails or `@domain` entries, and
    requires `email_verified=true`.
  - Without these settings, the original behavior is unchanged (the claim is the slug), so local dev still
    works with the mock IdP.
  - A shared `verifier_from_settings()` is used by both the API (`api/deps.py`) and the agent
    (`agent/identity.py`).
- **Optional LLM endpoint** (`agent/agent.py`): `LITELLM_API_BASE` may be empty, so `gemini/<model>` uses
  Google's endpoint directly.
- **Operator CLI** `backend/src/odn/db/provision.py` (`python -m odn.db.provision`), with subcommands
  `tenant`, `connection`, `role`, `budget` and `show`. Connections must use a Secret Manager reference
  named `odn-<tenant_id>-*`. That check (`is_tenant_secret_ref`) is now shared with the connection
  service.
- **Lockfile fix**: `backend/uv.lock` was missing `sqlalchemy[asyncio]`/`psycopg[binary]` for the
  `agent` extra, which `f2c82a3` had added to `pyproject.toml`. Images install with
  `uv sync --frozen`, so the agent image would have lacked them.
- **Tests**: Google domain mapping, default tenant, email allowlist and parser tests in
  `tests/tenancy/test_oidc.py`, plus provisioning tests in `tests/db/test_provision.py`. The full suite had
  212 passing tests and ruff was clean.

### Frontend

- `ODN_OIDC_API_TOKEN=id_token` makes the session forward Google's **ID token** to the API. Google access
  tokens are opaque and cannot be verified with JWKS. The session lifetime follows the ID token's `exp`
  (1 hour).
- `ODN_OIDC_PROMPT=select_account` lets users choose between a personal and a corporate Google account.
- Multi-stage `frontend/Dockerfile`: the `dev` target runs `next dev` locally, and the `prod` target runs
  the standalone `next build` output as the non-root `node` user. `next.config.js` sets
  `output: "standalone"`, and `docker-compose.yml` now selects `target: dev`.

### Compose, Caddy, Postgres

- **`docker-compose.prod.yml`** is an overlay on `docker-compose.yml`:
  - No mock OIDC or fake GCS (moved to a `local-only` profile).
  - No source mounts or `--reload`, and no host ports except Caddy's 80/443.
  - `restart: unless-stopped`.
  - Each service gets only the variables it needs (`env_file: !reset []`), with `${VAR:?}` checks for
    required ones.
  - Prod frontend image tagged `one-data-navigator-frontend:prod`.
  - API runs migrations on start without `--seed-local`.
- **`deploy/Caddyfile`**: automatic Let's Encrypt for `ODN_HOSTNAME`, HSTS and security headers, and a
  reverse proxy to `frontend:3000`.
- **`docker/postgres/init/90-prod-passwords.sh`**: on first database init, replaces the local-dev
  `odn_app`/`odn_agent` passwords with the prod ones. It does nothing locally.
- **`deploy/.env.prod.example`**: template for all prod settings. `.env.prod` files are gitignored.

### GCP scripts

- **`deploy/gcp/config.sh`**: project, region, zone, VM name and size, service account, bucket. All values can
  be overridden through environment variables.
- **`deploy/gcp/create-vm.sh`** (idempotent):
  - Enables the Compute, Secret Manager and IAP APIs.
  - Creates the `odn-vm` service account with `secretmanager.secretAccessor` and `logging.logWriter` on
    the project and `storage.objectAdmin` on `gs://datahop`.
  - Creates a static IP and firewall rules: 80/443 public, SSH only from the IAP range
    `35.235.240.0/20`.
  - Creates a daily snapshot schedule kept for 14 days.
  - Creates a Shielded VM with OS Login and attaches the snapshot schedule.
- **`deploy/gcp/vm-startup.sh`**: installs Docker Engine and the Compose plugin on first boot, and creates
  `/opt/odn/{releases,shared}`.
- **`deploy/deploy.sh`**: everything runs over IAP SSH.

  | Command | What it does |
  |---|---|
  | `env <file>` | Uploads the settings to `/opt/odn/shared/.env` (mode 600) |
  | `up` | Ships `git archive HEAD` to `/opt/odn/releases/<sha>`, links `current`, runs `compose up -d --build`, keeps 5 releases |
  | `ps`, `logs`, `ssh` | Status, log tailing and a shell on the VM |
  | `provision ...` | Runs the operator CLI |

### Pre-deploy verification (local)

The prod stack was started locally under a separate project name (`odn-smoke`) with dummy settings:

- Images built.
- The prod database password worked and the local-dev password was rejected.
- Migrations and tenant provisioning ran.
- `/` returned 200, and `/auth/login` redirected to Google with `prompt=select_account`.
- `caddy validate` passed.

The local stack also showed `DatabaseSessionService` saving and reading back a session in Postgres. A
problem caught here: `depends_on: !reset` with new values dropped the dependencies, so it was changed to
`!override`.

Documentation added or updated: [deploy_gcp_vm.md](deploy_gcp_vm.md) (runbook), the README and the dev plan
(deploy target, LLM and IdP decisions recorded).

## 4. Service-account permissions for creating the VM

The person or account running `create-vm.sh` needs:

| Action | Role |
|---|---|
| Enable APIs | `roles/serviceusage.serviceUsageAdmin` |
| Create `odn-vm` service account | `roles/iam.serviceAccountAdmin` |
| Grant project roles to it | `roles/resourcemanager.projectIamAdmin` |
| Grant bucket access | `roles/storage.admin` on `gs://datahop` |
| Static IP, VM, snapshot schedule, firewall | `roles/compute.admin` (or `instanceAdmin.v1` + `networkAdmin` + `securityAdmin`) |
| Attach `odn-vm` to the VM | `roles/iam.serviceAccountUser` |
| `deploy.sh` over IAP + sudo | `roles/iap.tunnelResourceAccessor`, `roles/compute.osAdminLogin` |
| Store connection keys | `roles/secretmanager.admin` |

Recommendation: run the one-time setup as a human admin (`gcloud auth login`), not with a long-lived
service-account key that has owner-level access. If a key is used, remove `projectIamAdmin`,
`serviceAccountAdmin` and `serviceUsageAdmin` after setup.

## 5. Deploy steps performed

1. **Create the VM**: `deploy/gcp/create-vm.sh`. It printed static IP `136.71.108.203`.
2. **DNS**: an `A` record `odn.datahop.ai` → `136.71.108.203`, confirmed with `dig +short odn.datahop.ai` (also
   against `8.8.8.8`).
3. **Google OAuth client**: Google Auth Platform, External audience, Web application client, redirect URI
   `https://odn.datahop.ai/auth/callback`.
4. **Fill in prod settings**: `deploy/.env.prod` was created from the template.
   - Random values were generated for the session secret (32 bytes, base64url), the three database
     passwords and the worker/agent tokens.
   - `ODN_GCS_BUCKET=datahop` and `ODN_GCS_PREFIX=odn` came from the local `.env`.
   - The LLM settings in the local `.env` were **not** reused, because they point at the corporate proxy.
   - The user added `ACME_EMAIL`, the OAuth client ID and secret, a Gemini API key, and
     `ODN_OIDC_DEFAULT_TENANT_EMAILS`.
5. **Readiness checks**:
   - No required values were empty.
   - The OAuth client ID ended in `.apps.googleusercontent.com`.
   - DNS resolved to the VM.
   - The Gemini key listed models: `gemini-3.5-flash` was available and `generateContent` returned 200.
   - IAP SSH worked. The VM had Docker 29 / Compose v5, 15 GB free RAM and 45 GB free disk, and could
     reach the Gemini API.
   - `HEAD` was pushed.
6. **Deploy**: `deploy/deploy.sh env deploy/.env.prod` then `deploy/deploy.sh up`. All 6 containers came
   up:
   - API `/readyz` returned `{"postgres":"ok","gcs":"ok"}`.
   - The agent `/healthz` returned 200.
   - Migrations applied and the prod database passwords were set on first init.
7. **TLS failure and fix**: Caddy could not get a certificate because `ACME_EMAIL` had a typo
   (`...@gmail.comwan`); Let's Encrypt reported an invalid contact. After correcting it in
   `deploy/.env.prod`, re-uploading with `deploy.sh env`, and force-recreating `caddy`, the logs showed
   `certificate obtained successfully`:
   - `https://odn.datahop.ai/` returned 200.
   - HTTP redirected to HTTPS (308).
   - `/auth/login` redirected (307) to `accounts.google.com`.
8. **`deploy.sh` fix** (`569bccd`): the final `compose ps` in `up` ran from the releases directory after
   cleanup and printed a harmless error. It now runs from `current`.
9. **Tenants**:
   ```sh
   deploy/deploy.sh provision tenant datahop --name DataHop
   deploy/deploy.sh provision tenant cotality --name Cotality
   ```
10. **First sign-in** with a personal Google account (on the `datahop` allowlist) worked. The user was then
    promoted:
    ```sh
    deploy/deploy.sh provision role datahop <email> admin
    ```
11. **Cotality BigQuery connection**:
    - The allowlist was generated from the local `.env` (`BQ_PROJECT_ID`, `BQ_DATASET_IDS`, `BQ_TABLES`)
      with the same parser as local seeding. It has 25 datasets (`project.dataset.*`) plus 2 tables in
      `edr_pmd_property_pipeline`, and default dataset `stg_property_firmographics`.
    - A one-off script (kept outside the repo) ran
      `deploy/deploy.sh provision connection cotality idap-int --project clgx-idap-bigquery-int-278e --secret-ref projects/datahop-514819/secrets/odn-<cotality tenant id>-bq-idap/versions/latest --default-dataset ... --allow ...`.
      It created connection `1d65fa94-e6d1-450e-a77c-0eabd69923c5`.
12. **Missing secret, then fixed**: a test from the VM failed with Secret Manager `NotFound`, because the
    secret had not been created yet. The secret was then created:
    ```sh
    TID=0a86ccd8-8d9b-45db-917e-9520f63ec989
    gcloud --project datahop-514819 secrets create "odn-$TID-bq-idap" --replication-policy automatic
    gcloud --project datahop-514819 secrets versions add "odn-$TID-bq-idap" --data-file keys/<cotality-sa>.json
    ```
    The test was re-run inside the API container. The secret was read (Cotality ETL service account), all
    four connection checks passed (auth, network, metadata, read), and a dry-run succeeded. No redeploy was
    needed, because the connection reads `versions/latest`.
13. **`provision show` fix** (`2ce28ec`): `show` listed every connection and user under every tenant. The
    data was correct: a direct SQL check showed the connection under `cotality` and the user under
    `datahop`. The cause was that the Postgres owner role `odn` is a superuser, which bypasses RLS, so
    `show` relied on RLS filtering that never happened. It now filters by `tenant_id` explicitly; the other
    provision commands already did. Redeployed with `deploy.sh up`.
14. **End-to-end check by the user**: signed in with a `cotality.com` Google account. It landed in the
    `cotality` tenant through the `hd` claim, the `idap-int` datasets were listed, and queries and chat
    worked.
15. **Dev plan updated** (`acb2cb5`): the MVP is recorded as live on `odn.datahop.ai`.

## 6. Commits made today

| Commit | Summary |
|---|---|
| `f2c82a3` | Durable ADK chat sessions in Postgres |
| `46ad12a` | Migration 0006: `adk` schema for durable agent sessions |
| `ffb97c8` | Single GCP VM production deploy (Compose + Caddy + Google sign-in), provisioning CLI, scripts, docs, `uv.lock` fix |
| `569bccd` | `deploy.sh`: run final `compose ps` from the current release |
| `2ce28ec` | `provision show`: filter rows by tenant explicitly |
| `acb2cb5` | Dev plan: MVP live on `odn.datahop.ai` |

## 7. Day-to-day operations

Run all of these from the repo root on the Mac.

```sh
git commit ...                                   # only committed HEAD is deployed
deploy/deploy.sh up                              # ship + rebuild + restart
deploy/deploy.sh ps                              # status
deploy/deploy.sh logs api agent caddy            # follow logs
deploy/deploy.sh ssh                             # shell in /opt/odn/current
deploy/deploy.sh env deploy/.env.prod            # after editing settings, then: deploy.sh up
deploy/deploy.sh provision show                  # tenants, connections, users
deploy/deploy.sh provision role <tenant> <email> <member|data_lead|admin>   # after the user's first sign-in
deploy/deploy.sh provision budget <tenant> <tokens_per_day>
```

To allow another personal account, add it to `ODN_OIDC_DEFAULT_TENANT_EMAILS` in `deploy/.env.prod`, then run
`deploy.sh env` and `deploy.sh up`. If the OAuth consent screen is still in *Testing*, also add the
account as a test user.

## 8. Known limits and follow-ups

- **Sessions last 1 hour** (the Google ID token lifetime). OIDC token refresh is still to do.
- **Shared VM identity**: every container can use the VM service account through the metadata server, so it
  can read the bucket and the project's secrets. To narrow this, grant `secretAccessor` on the individual
  secret instead of the project.
- **Cotality key in `datahop-514819`**: confirm with Cotality that storing it outside their organization is
  permitted.
- **Database passwords** apply only on first init. Rotate with `ALTER ROLE ... PASSWORD` and update `.env`.
- **Backups**: daily boot-disk snapshots (kept 14 days). To restore, create a disk from a snapshot.
- **No CI**: run `cd backend && uv run pytest && uv run ruff check src tests` before `deploy.sh up`.
- The IAP tunnel warning about NumPy is harmless. Installing NumPy for the gcloud Python speeds up the
  tunnel.
- Next items from the dev plan: run the Phase 1 and Phase 2 exit checks on the hosted URL, summary stats
  for the LLM, the gold benchmark, and dictionary/accessibility tests.
