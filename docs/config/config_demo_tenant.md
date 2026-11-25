# Demo tenant: "Start a live demo"

**Status (2026-11-24): ✅ live on `https://easyquery.ai`.** A visitor clicks **Start a live demo**, enters a name and
email (not verified) and goes straight into the app as a member of **Demo**, a mock enterprise client, querying the
curated **Demo store** dataset and Google BigQuery public data. Releases `1b7483c` (demo), `c6a0dcf` (P1, P2, P3,
P7, P8, P9, P10). Design notes: [Tenant_Isolation_User_system.md §4.3b](../Tenant_Isolation_User_system.md).

## 0. Summary: done, to do, proposed

### Done

| Area | What | Release / where |
|---|---|---|
| Entry points | "Start a live demo" on the landing page (header, first and last slide), `/signin` ("Just looking?") and the Demo organization's page `easyquery.ai/demo` ("Visiting?") | `1b7483c` |
| Check-in page | `/try`: name + email, then straight into the app; 404 when `ODN_DEMO_TENANT` is empty; `try` is a reserved organization slug | `1b7483c` |
| Check-in API | `POST /v1/auth/demo`: creates or reopens the visitor's demo user, records the check-in, returns an API-signed session token | `1b7483c` |
| Demo identity | Tokens from issuer `urn:querymuse:demo`, signed with the app signing key (`ODN_COLLIBRA_SIGNING_KEY_SECRET`, Secret Manager) and published at `/.well-known/jwks.json`; API and agent verify them like any other issuer; mapped to the tenant through `tenant_identity_providers` | `1b7483c` |
| Check-in records | Every check-in (name, email, time, demo user, kind: `new`, `returning`, `fresh`, `link`) in `demo_checkins` (RLS-scoped to the tenant) | migrations `0027_demo`, `0028_demo_checkin_kind` |
| Starter prompts (P1) | Demo visitors get five starter questions (four on the Demo store, one on public data) and a Demo store placeholder; the trial-key banner is replaced by the demo banner | `c6a0dcf` |
| Visitors in the admin UI (P2, P10) | Organization tab of the demo organization → **Demo visitors**: funnel by person/email (checked in, new, asked a question, got a data answer, came back) for 7/30/90 days, the check-ins with visit kind and per-user questions/queries, and **Download CSV** (formula-safe). API `GET /v1/admin/demo/funnel`, `/checkins`, `/checkins.csv` (admins) | `c6a0dcf` |
| Notifications (P3) | Email to `ODN_DEMO_NOTIFY_EMAIL` (prod: `support@easyquery.ai`) for every **new** visitor, sent after the response (failures logged, never shown) | `c6a0dcf`, prod `.env` |
| Upgrade path (P8) | Demo banner "Keep using EasyQuery": **Create your free personal account** (signs out of the demo, opens personal Create account with name/email prefilled) or **talk to sales** (mailto support with name/email). After the personal account's first sign-in with a **verified** email, "We found N chats from your EasyQuery demo — Import them / Not now": the agent moves the conversations (`/demo-chats/import`), the API retires those demo users | `c6a0dcf` |
| Curated data (P9) | BigQuery dataset `datahop-514819.easyquery_demo` (US): `orders`, `order_items`, `products`, `customers`, `distribution_centers`, a snapshot of theLook e-commerce with table/column descriptions, no names/emails/addresses (~31 MB). Script: [deploy/demo/easyquery_demo.sql](../../deploy/demo/easyquery_demo.sql). Service account `odn-demo-bq` (BigQuery Job User + Read Session User on the project, READER on this dataset only); key in Secret Manager `odn-b1f425a9-5379-4878-8a75-a962442d03a5-bq`. Tenant-shared connection **Demo store** (`949e4a9c-…`) in `demo`, allowlist `datahop-514819.easyquery_demo.*` | `c6a0dcf`, prod |
| Returning visitors (P7) | Check-in always starts instantly. The **same browser** reopens its demo user via a device token (httpOnly cookie `odn_demo_device`, signed by the API, bound to the email, ≤ 7 days). Elsewhere, an email that already has demo chats gets a **fresh** demo user (no email on it) and the inbox gets a one-hour **link** (`/try/continue`) to reopen the earlier chats, so only the inbox owner gets them back. A demo user lasts 7 days, then the email starts fresh; a session never outlives its demo user | `c6a0dcf` |
| Guards | Staff emails (real accounts in `demo`) refused (409); admin-disabled demo users stay refused (403); rate limits per email (10/h), per client address (20/h) and overall (500/h) | `1b7483c` |
| Data | Demo users use the platform `bigquery-public-data` connection (billing `datahop-514819`); per-query scan cap **25 GB** (`tenants.max_bytes_billed`, new per-tenant cap enforced in `run_query`) | prod DB |
| Isolation | Sharing turned off in `demo` (visitors can't see each other's answers); no tenant-shared connections created by us | `provision demo` |
| Admin | `support@easyquery.ai` invited as **admin** of `demo` (set-password email); signs in at `easyquery.ai/demo` (domain-less password tenants are now discoverable); can share connections/datasets with all visitors in **Settings → Connections** and manage demo users | prod DB, Identity Platform tenant `demo-zjhs9` |
| Prod config | `demo` tenant `b1f425a9-5379-4878-8a75-a962442d03a5`, `ODN_DEMO_TENANT=demo` (api, agent, frontend); `.env` backup `/opt/odn/shared/.env.bak-*` | prod |
| Verified on prod | 2026-11-23: `/try` 200; check-in → `demo` member; platform connection catalog; a real BigQuery query; agent accepts demo tokens and rejects bad ones; links on `/`, `/signin`, `/demo`. 2026-11-24: new browser → fresh + link email; same browser → reopened; Demo store catalog and a revenue query through `odn-demo-bq`; new-visitor email sent; agent `/demo-chats` answers. Locally: banner, starter prompts, placeholder, upgrade link prefill | 2026-11-24 |
| Tests | `backend/tests/db/test_demo.py` (check-in kinds, device token, link, expiry, token lifetime, refusals, admin check-ins/CSV/funnel, import gating), `tests/agent/test_conversations.py` (import moves only the claimed demo chats), scan cap in `tests/query/test_query_service.py`; full suite 412 passed | `c6a0dcf` |

### To do

| # | Item | Why / notes |
|---|---|---|
| T1 | Run one full **chat** as a demo visitor on prod | Verified everything except an end-to-end agent answer (local LLM gateway `llm-uat.cotality.com` was unreachable). |
| T2 | `support@easyquery.ai` sets a password and signs in at `easyquery.ai/demo` | Confirms the admin path (invite email → set password → sign in → Settings → Connections). |
| T3 | Review the Demo store and public datasets | Demo store (curated) is the main demo data. The platform connection still allows only `samples`, `usa_names`, `austin_bikeshare`, `austin_311`, `austin_crime` and is shared by every organization; the Demo admin can share more demo-only connections in Settings. |
| T4 | Set a **demo LLM budget** | `demo` uses the defaults: 2M tokens/day for the whole tenant, plus a 200k/day trial per visitor. Set explicitly: `deploy/deploy.sh provision budget demo <tokens>`. |
| T5 | Mention demo check-in data in **/privacy** | We store name + email (unverified) to follow up; the check-in page says so, the privacy page should too. |
| T7 | Check the **Demo visitors** section and the **import** banner in a real browser on prod | Covered by API tests; needs an admin sign-in (T2) and a personal account created from the demo banner. |
| T8 | Refresh the Demo store snapshot occasionally | Dates are frozen at 2026-11-24; prompts say "last 12 months". Re-run `deploy/demo/easyquery_demo.sql`. |
| T6 | Fix the **local** demo setup | Local `bigquery-public-data` platform row now points at `file:datahop-514819-d24f3a966b27.json` (`navigator-bff-sa`), which lacks `bigquery.jobs.create` → local queries fail. Use a key with BigQuery Job User (see [bigquery-public-data-setting.md](bigquery-public-data-setting.md)). |

### Proposed (optimizations)

Done: P1, P2, P3 (instant email), P7, P8, P9, P10 (see Done). Still open:

| # | Idea | Benefit |
|---|---|---|
| P3b | **Daily digest** of check-ins and/or **Slack** per new visitor | Less inbox noise; team channel visibility. |
| P4 | **Retention**: purge retired demo users, their chats/artifacts and old `demo_checkins` after N days | Bounded storage and personal data. |
| P5 | **Shared rate limits**: limits are in-process (single API process today) | Needed if the API runs more than one process/replica. |
| P6 | **Bot protection** on `/try` (Turnstile/reCAPTCHA) if abuse appears | Rate limits cap cost but not fake check-ins. |
| P8b | Import also copies stored **result files and charts** (today: conversations only; Demo store data is not reachable outside the demo) | Fully carried-over chats. |
| P11 | Funnel by **traffic source** (UTM on `/try`) and a weekly trend | Know which channels bring visitors who ask questions. |

## 1. How it works

```
Landing / signin / easyquery.ai/demo
        │  "Start a live demo"
        ▼
/try (name, email) ──POST /auth/demo (BFF: client IP + device cookie)──► API POST /v1/auth/demo
                                                                 │ odn.platform.demo.check_in
                                                                 │  • device token for this email → reopen (returning)
                                                                 │  • email has current chats → fresh user + email link
                                                                 │  • else → new demo user (+ email to support)
                                                                 │  • insert demo_checkins row (kind)
                                                                 │  • sign session JWT (≤ 8 h) + device token (≤ 7 d)
        ◄────── session cookie (provider "demo") + odn_demo_device ──────┘
        ▼
App as a Demo member → API/agent verify token via /.well-known/jwks.json → tenant `demo`
        → Demo store (curated) + platform bigquery-public-data (+ any connections the Demo admin shares)
        → banner: create a personal account (prefilled) / talk to sales

Email link /try/continue?token=… ──POST /auth/demo/link──► API POST /v1/auth/demo/link → reopens the earlier demo user

Personal account (verified email) ──/api/agent/demo-chats──► agent asks API /v1/me/demo-chats which demo users
        qualify → "Import them" → agent copies the sessions, deletes the demo ones → API retires those demo users
```

- **No refresh token**: when the session (8 h, `ODN_DEMO_SESSION_HOURS`) ends, the visitor checks in again; the same
  browser reopens the same demo user while it is under 7 days old (`ODN_DEMO_ACCOUNT_DAYS`).
- Device and link tokens use other audiences than the session token, so they never work as API bearer tokens.
- **Everything shared in `demo` is visible to every visitor**: only share demo data there.

### Code map

| Piece | File |
|---|---|
| Migrations (check-ins, scan cap, demo lookup, discoverable password tenants, `try` reserved; check-in kind) | [0027_demo.sql](../../backend/src/odn/db/migrations/0027_demo.sql), [0028_demo_checkin_kind.sql](../../backend/src/odn/db/migrations/0028_demo_checkin_kind.sql) |
| Check-in, device/link tokens, import eligibility | [demo.py](../../backend/src/odn/platform/demo.py) |
| Notifications and link email | [demo_notify.py](../../backend/src/odn/platform/demo_notify.py), [auth_emails.py](../../backend/src/odn/platform/auth_emails.py) |
| Routes `POST /v1/auth/demo`, `/v1/auth/demo/link`, rate limits | [auth_routes.py](../../backend/src/odn/api/auth_routes.py) |
| Admin check-ins, CSV, funnel | [admin_routes.py](../../backend/src/odn/api/admin_routes.py) |
| Import eligibility `/v1/me/demo-chats` | [me_routes.py](../../backend/src/odn/api/me_routes.py) |
| Import (moves conversations) | [agent/main.py](../../backend/src/odn/agent/main.py) |
| Curated dataset | [easyquery_demo.sql](../../deploy/demo/easyquery_demo.sql) |
| Demo issuer (`DEMO_ISSUER`, verifier) | [oidc.py](../../backend/src/odn/tenancy/oidc.py) |
| Per-tenant scan cap | [context.py](../../backend/src/odn/tenancy/context.py), [query/service.py](../../backend/src/odn/platform/query/service.py) |
| `provision demo`, `provision checkins` | [provision.py](../../backend/src/odn/db/provision.py) |
| Settings `demo_tenant`, `demo_account_days`, `demo_session_hours` | [config.py](../../backend/src/odn/config.py) |
| Check-in page and form | [try/page.tsx](../../frontend/app/try/page.tsx), [DemoCheckIn.tsx](../../frontend/app/components/DemoCheckIn.tsx) |
| BFF routes (session + device cookie, email link) | [lib/demo.ts](../../frontend/lib/demo.ts), [auth/demo/route.ts](../../frontend/app/auth/demo/route.ts), [auth/demo/link/route.ts](../../frontend/app/auth/demo/link/route.ts), [try/continue/page.tsx](../../frontend/app/try/continue/page.tsx) |
| Starter prompts, demo banner, import banner | [DemoBanners.tsx](../../frontend/app/components/DemoBanners.tsx), [AppShell.tsx](../../frontend/app/components/AppShell.tsx) |
| Demo visitors (admin) | [Organization.tsx](../../frontend/app/components/Organization.tsx) |
| Entry links | [LandingDeck.tsx](../../frontend/app/components/LandingDeck.tsx), [signin/page.tsx](../../frontend/app/signin/page.tsx), [[slug]/page.tsx](../../frontend/app/[slug]/page.tsx) |
| Tests | [test_demo.py](../../backend/tests/db/test_demo.py), [test_conversations.py](../../backend/tests/agent/test_conversations.py) |

## 2. Setup (runbook)

Prerequisites: the app signing key (`ODN_COLLIBRA_SIGNING_KEY_SECRET`) and the platform `bigquery-public-data`
connection exist. Then, with `deploy/deploy.sh provision …` on prod (or `python -m odn.db.provision …` locally):

```sh
# 1. The tenant (password sign-in for its admins only; visitors never need an account)
provision tenant demo --name Demo --auth password --join invite --create-idp-tenant
# 2. Demo issuer mapping, sharing off, 25 GB per-query cap
provision demo demo --max-gb 25
# 3. Its admin (set-password email; signs in at /demo)
provision invite demo support@easyquery.ai --role admin
# 4. Optional: the tenant's daily LLM budget
provision budget demo 2000000
# 5. Curated data: dataset, then a service account (BigQuery Job User + Read Session User on the project, READER on
#    the dataset) whose JSON key is stored as Secret Manager secret odn-<demo tenant id>-bq, then the connection
bq --project_id=datahop-514819 --location=US mk --dataset datahop-514819:easyquery_demo
bq --project_id=datahop-514819 --location=US query --use_legacy_sql=false < deploy/demo/easyquery_demo.sql
provision connection demo "Demo store" --project datahop-514819 --default-dataset easyquery_demo --location US \
  --secret-ref projects/datahop-514819/secrets/odn-<demo tenant id>-bq/versions/latest --allow 'datahop-514819.easyquery_demo.*'
```

Then set `ODN_DEMO_TENANT=demo` and `ODN_DEMO_NOTIFY_EMAIL=support@easyquery.ai` in the env (api, agent and frontend
read the tenant; the personal-account import also needs `ODN_PERSONAL_TENANT`) and restart:

```sh
# prod: edit /opt/odn/shared/.env on the VM (back it up first), then
sudo docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d api agent frontend
```

Turn the demo off: empty `ODN_DEMO_TENANT` and restart (links disappear, `/try` returns 404, check-ins 404), or
suspend the tenant (`provision tenant demo --name Demo --status suspended`).

## 3. Operations

| Task | How |
|---|---|
| Recent check-ins, funnel, CSV | Demo admin: Organization tab → **Demo visitors**; or `deploy/deploy.sh provision checkins demo --limit 100` |
| Refresh the Demo store snapshot | `bq --project_id=datahop-514819 --location=US query --use_legacy_sql=false < deploy/demo/easyquery_demo.sql` |
| Change who gets new-visitor emails | `ODN_DEMO_NOTIFY_EMAIL` (empty = none), restart api |
| Add data for visitors | Demo admin: **Settings → Connections** → share with the organization (BigQuery key + allowlist, or Postgres) |
| Block a visitor | Demo admin disables the user (Organization tab) or `provision user demo <email> --status disabled`; refused until that demo user's 7 days end |
| Change the scan cap | `provision demo demo --max-gb <N>` (omit `--max-gb` to remove the cap) |
| Change the LLM budget | `provision budget demo <tokens>` |
| Session / reuse windows | `ODN_DEMO_SESSION_HOURS` (default 8), `ODN_DEMO_ACCOUNT_DAYS` (default 7), in the prod `.env` |

## 4. Security notes

- Check-ins are **unverified**: the email is a label, not an identity. Demo users are `member`s of an isolated
  tenant with sharing off; they can never reach other organizations, and staff emails cannot be used to check in.
- Only the same browser (device token) or the inbox owner (emailed link) can reopen earlier demo chats; typing
  someone's email only starts a fresh demo (and emails them a link, rate-limited to 3 per hour per email).
- Chat import needs a personal account whose sign-in proved the email (verified) and an explicit "Import them";
  anyone could have checked in with that email, so the banner shows the chat titles first.
- Never share non-demo data in `demo`: every visitor sees tenant-shared connections.
- Demo tokens are signed with the same key as Collibra tokens; rotating `ODN_COLLIBRA_SIGNING_KEY_SECRET` ends all
  demo sessions (visitors just check in again).
