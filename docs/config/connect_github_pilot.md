# EasyQuery: Use GitHub Copilot as the AI Model

This guide covers how EasyQuery lets users run the chat on **their own GitHub Copilot seat**: the one-time GitHub
setup, the server configuration, what each user does, and how to troubleshoot it.

## Overview

- **What users get:** in **Settings → AI model**, the provider **GitHub Copilot (sign in with GitHub)**. After
  signing in, the Model list shows the models their Copilot seat allows (for example `claude-sonnet-5.5`,
  `claude-opus-5.5`, `gpt-6.2-sol`, `gemini-4-argon`).
- **Billing:** usage counts against each user's own Copilot plan (for Cotality, the company's Copilot
  subscription), not the EasyQuery platform key. The chat records it as the user's own key, so the free-trial
  budget is not used.
- **Which models:** decided by the user's organization's Copilot policy. EasyQuery only lists what GitHub returns.
- **Official path only:** sign-in uses GitHub's OAuth **Device Flow** with EasyQuery's own GitHub OAuth App, and
  model calls go through the official [GitHub Copilot SDK](https://docs.github.com/en/copilot/how-tos/copilot-sdk)
  (`microsoft-copilot-sdk`). EasyQuery never uses undocumented Copilot endpoints or another app's tokens.

## How it works

```
User ── Settings → AI model → "Sign in with GitHub"
   │   1. EasyQuery shows a one-time code (e.g. ACF0-7E1B)
   │   2. User opens github.com/login/device, pastes it, authorizes "EasyQuery"
   ▼
API (odn.platform.github_copilot)
   - polls GitHub until approved, reads the GitHub username
   - checks the account has Copilot access (lists its models)
   - stores the GitHub token encrypted (like any BYO key), shown as "@username"
   ▼
Agent (odn.agent.budget → odn.agent.copilot_llm)
   - for each model call ADK makes, opens a short Copilot SDK session with the user's token
   - the session gets ADK's system prompt and ONLY EasyQuery's tools (no Copilot shell/files/web)
   - tools are "terminal": a tool call ends the session and goes back to ADK
   - ADK runs the tool (allowlists, approvals, guardrails) and calls the model again with the result
```

| Piece | Where |
|---|---|
| Device flow, model listing | `backend/src/odn/platform/github_copilot.py` |
| Routes `GET/POST /v1/me/llm-key/github`, `…/github/start`, `…/github/poll` | `backend/src/odn/api/me_routes.py` |
| Provider `github_copilot` (stored in `user_llm_keys`) | `backend/src/odn/platform/llm_keys.py`, migration `0032_github_copilot_llm.sql` |
| Model adapter (Copilot SDK ↔ ADK) | `backend/src/odn/agent/copilot_llm.py`, routed in `backend/src/odn/agent/budget.py` |
| Sign-in UI | `frontend/app/components/Settings.tsx` (`GitHubSignIn`) |
| Copilot runtime in the image | `backend/Dockerfile` (downloaded and checksum-verified at build time) |

## One-time setup: register the GitHub OAuth App

Done once per EasyQuery deployment. The current app is **EasyQuery**, client ID `Ov23li5BQ37yXTCdZErR`.

1. Open <https://github.com/settings/applications/new> (a personal account works; it can be transferred to a
   GitHub organization later under **Settings → Developer settings → OAuth Apps → Transfer ownership**).
2. Fill in:

   | Field | Value |
   |---|---|
   | Application name | `EasyQuery` |
   | Homepage URL | `https://easyquery.ai` |
   | Authorization callback URL | `https://easyquery.ai/settings/github/callback` (required by the form, unused by Device Flow) |

3. Click **Register application**.
4. On the app page, tick **Enable Device Flow** and click **Update application**.
5. Copy the **Client ID** (starts with `Ov23li…`). It is a public identifier.
6. **Do not generate a client secret.** Device Flow doesn't need one, so there is nothing secret to store.

Use the **OAuth App** form, not the **GitHub App** form (the GitHub App form calls the field "Redirect URI").

## Server configuration

One setting turns the feature on. When it is empty the GitHub Copilot option is hidden.

```bash
ODN_GITHUB_CLIENT_ID=Ov23li5BQ37yXTCdZErR
```

### Production (GCP VM)

The prod API reads it through `docker-compose.prod.yml` (`ODN_GITHUB_CLIENT_ID: ${ODN_GITHUB_CLIENT_ID:-}`).
Add the line to the server's settings file, then deploy:

```bash
# Safest: append the one line on the VM (keeps every other live setting; takes a backup first).
source deploy/gcp/config.sh
gcloud compute ssh "$ODN_VM" --project "$GCP_PROJECT" --zone "$GCP_ZONE" --tunnel-through-iap --command '
  F=/opt/odn/shared/.env   # $ODN_REMOTE_DIR/shared/.env
  sudo cp -p $F $F.bak-$(date +%Y%m%d%H%M%S)
  sudo grep -q "^ODN_GITHUB_CLIENT_ID=" $F || echo "ODN_GITHUB_CLIENT_ID=Ov23li5BQ37yXTCdZErR" | sudo tee -a $F'

deploy/deploy.sh up
```

> `deploy/deploy.sh env deploy/.env.prod` **replaces the whole file** on the server. Only use it if your local
> `deploy/.env.prod` is up to date with every live setting; otherwise append the single line as above.

### Local development

Add the same line to the repo-root `.env`, then restart the API:

```bash
echo "ODN_GITHUB_CLIENT_ID=Ov23li5BQ37yXTCdZErR" >> .env
docker compose up -d api agent
```

The backend image must include the SDK and its runtime. After pulling these changes, rebuild once:

```bash
docker compose build api agent && docker compose up -d api agent
```

**Behind Zscaler (Cotality network):** Docker traffic goes through TLS inspection, so containers can't verify
GitHub's certificate and the runtime download fails with `CERTIFICATE_VERIFY_FAILED`. Export the Zscaler root
from the macOS keychain and point the build at it (the file is git-ignored; prod builds don't use it):

```bash
security find-certificate -c "Zscaler Root CA" -p > keys/extra-ca.pem
echo "ODN_EXTRA_CA_FILE=./keys/extra-ca.pem" >> .env
docker compose build api agent
```

## Each user: sign in and pick a model

1. Open EasyQuery → **Settings** → **AI model**.
2. Provider: **GitHub Copilot (sign in with GitHub)** (listed first), then **Sign in with GitHub**.
3. Click **Copy** next to the one-time code, then **Open github.com/login/device**.
4. Paste the code on GitHub and click **Authorize EasyQuery**. The code is valid for 15 minutes.
5. Back in EasyQuery (it checks every few seconds) the status turns to **GitHub Copilot is active ·
   Signed in as @username** and the **Model** list fills in.
6. Pick a model (`claude-sonnet-5.5` is a good default) and click **Save**.

To switch accounts use **Use another GitHub account**; to stop using Copilot click **Sign out** (the chat falls back
to the shared trial model or another key).

EasyQuery only asks for the `read:user` scope: it reads the GitHub username and uses the token for Copilot. It
never reads repositories or code.

## Organization approval (Cotality and other GitHub organizations)

If a GitHub organization restricts third-party OAuth Apps, the first user sees **Request approval** on GitHub
instead of being connected. A GitHub **organization owner** then approves "EasyQuery" once under
**Organization settings → Third-party access → OAuth application policy**. After that, everyone in the
organization signs in normally. (Signing in to DBeaver's Copilot integration worked for Cotality users, which
suggests the organization does not restrict OAuth Apps today.)

## Verify

```bash
# Server: feature on, runtime present, provider allowed (prod: run inside the VM's compose project)
docker compose exec -T api python -c 'from odn.platform import github_copilot as g; print(g.enabled())'
docker compose exec -T agent sh -c 'ls /opt/copilot-runtime/prebuilds/*/copilot'
docker compose exec -T postgres psql -U odn -d odn -Atc \
  "select pg_get_constraintdef(oid) from pg_constraint where conname='user_llm_keys_provider_check'"

# After a user signs in: provider, model and @login (never the token)
docker compose exec -T postgres psql -U odn -d odn -Atc \
  "select provider, model, key_hint, updated_at from user_llm_keys where provider='github_copilot'"

# After a chat: usage recorded as the user's own key
docker compose exec -T postgres psql -U odn -d odn -Atc \
  "select model, key_source, requests, prompt_tokens from llm_usage order by updated_at desc limit 5"
```

In the app, ask a data question: the agent steps (`list_connections`, `get_table_schema`, `run_sql`, …) appear as
usual, expensive scans still ask for approval, and the answer shows folded SQL.

## Troubleshooting

| Problem | Cause | Fix |
|---|---|---|
| No "GitHub Copilot" provider in Settings | `ODN_GITHUB_CLIENT_ID` is empty for the API | Set it and restart the API (prod: append to the server `.env`, `deploy/deploy.sh up`). |
| "Sign in with GitHub isn't set up on this server" | Same as above | Same as above. |
| GitHub says "Expired user code" | The code is only valid for 15 minutes | Click **Sign in with GitHub** again for a fresh code. |
| GitHub shows "Request approval" | The organization restricts OAuth Apps | An org owner approves EasyQuery once (see above). |
| "Signed in as @x, but this GitHub account has no GitHub Copilot access" | The account has no Copilot seat, or the org's policy blocks it | Assign a Copilot seat to the user, or sign in with the account that has one. |
| A model is missing from the list | The organization's Copilot policy doesn't enable it | Ask the Copilot admin to enable the model; EasyQuery only lists what GitHub returns. |
| Chat replies "GitHub Copilot couldn't answer with model …" | Token revoked, seat removed, model disabled, or GitHub unavailable | Sign in with GitHub again or pick another model in Settings → AI model. |
| Local build fails with `CERTIFICATE_VERIFY_FAILED` downloading the runtime | Zscaler TLS inspection | Export the Zscaler root to `keys/extra-ca.pem` and set `ODN_EXTRA_CA_FILE` (see Local development). |

## Proposed improvements

Observed in the first end-to-end test (one question, one follow-up): **9 model calls, ~95k prompt tokens
(~10.5k per call), 2.7k output tokens**. Every call resends the system prompt (~9.4k chars) and all tool schemas
(~10.9k chars), because each ADK model call is a fresh Copilot session.

Measured after adding per-call logging (`odn.agent.copilot_llm: copilot call …`):

- **Prompt caching already works:** ~8.4k of ~8.7k input tokens per call are cache reads (~97%), and Copilot prices
  cached input ~20× below normal input. The fixed prefix is therefore cheap; a typical question costs about 1 AI
  credit on `claude-sonnet-5.5` (≈ 0.05 on `claude-haiku-5.5`).
- **Every model call reports a premium cost of 1.0** (`assistant.usage.cost`), so the *number of calls* per question
  drives the allowance more than tokens do. Fewer round trips (P2 session reuse) is the main remaining lever.
- GitHub's allowance numbers (`premium_interactions`) are not plain request counts (they moved ~600 for 11 calls);
  EasyQuery shows them as GitHub reports them.

Status: **P1 items 1–3 are done** (✅ below). The rest is ordered by value.

| # | Improvement | Why | Effort |
|---|---|---|---|
| ✅ P1 | **Show Copilot quota and cost in Settings.** Allowance meter (% left, used/entitlement) and "Today in EasyQuery: N model calls · tokens · ≈ AI credits (estimate)", priced per model with cached input at the cache price (`llm_usage.cached_tokens`, migration 0033); refreshes when the AI model section opens. | Done. Not yet shown in the chat itself. | S |
| ✅ P1 | **Recommend models by cost.** Model list grouped Low / Medium / High cost (GitHub's categories), cheapest first, with relative cost ("1×", "20×", "40×") and a **recommended** tag; new sign-ins start on the strongest low-cost model (today `gpt-6.1-terra`) instead of `auto`. | Done. | S |
| ✅ P1 | **Trim what each call resends.** Only the latest tool results go in full (30k chars); earlier ones are clipped to 2k. Per-call sizes, input/cached/output tokens and premium cost are logged (no content); a session start that fails on a passing network error is retried once. | Done. Caching already makes the fixed prefix cheap (see above). | S |
| P2 | **Keep one Copilot session per conversation turn.** Instead of a new session per ADK call, keep the session open while ADK runs a tool: the tool handler waits for ADK's result and returns it, so the model continues in the same session (no transcript resend, better provider-side caching). Fall back to the stateless path on any mismatch. | Biggest token and latency win (fewer resends, fewer premium requests), but needs care with timeouts and multiple agent replicas. | M |
| P2 | **Stream the answer.** Forward the SDK's message-delta events to the chat instead of waiting for the final message. | Answers appear as they are written, like the other providers. | M |
| P2 | **Short-lived, refreshable tokens.** Move from an OAuth App (long-lived `gho_` token) to a GitHub App with user-to-server tokens (`ghu_`, 8-hour expiry + refresh token) and pass them through the SDK's `token_provider`. | A leaked token stops working within hours; GitHub's recommended model for SaaS apps. | M |
| P2 | **Re-sign-in nudges.** When GitHub rejects a token (revoked app, removed seat), mark the saved key as needing sign-in and show a banner with **Sign in with GitHub** instead of a chat error. | Turns a confusing failure into one click. | S |
| P3 | **Organization-billed option.** A GitHub App installed on the company's GitHub org with the **Copilot Requests** permission, billed to the org (server-to-server tokens). Users without a personal Copilot seat, or a whole EasyQuery tenant, could then use Copilot without signing in. | Covers teammates without seats; one admin setup instead of per-user sign-in. | M |
| P3 | **Tenant admin controls.** In Organization settings: make GitHub Copilot the default provider for the tenant, allow-list models, and see per-user Copilot usage. | Lets the data lead standardize on approved models and watch spend. | M |
| P3 | **Own the OAuth App in an organization.** Transfer "EasyQuery" from a personal account to the company's or EasyQuery's GitHub organization, add the logo and a privacy-policy link to the consent screen. | Removes a single-person dependency; users see a trusted publisher when authorizing. | S |
| P3 | **Pin and watch the SDK.** Pin `microsoft-copilot-sdk` to a tested minor version, add a nightly smoke test (list models + one tool call with a test account), and track SDK breaking changes. | The SDK is new and moving quickly; catch breakage before users do. | S |

Docs follow-ups: split this page into an **admin guide** (OAuth App, server config, org approval) and a short
**user guide** with screenshots of the sign-in panel, and add an FAQ ("Does EasyQuery see my code?" No: it only
reads your GitHub username).

## Security notes

- The GitHub token is encrypted at rest with `ODN_SECRETS_KEY` (AES-256-GCM, bound to the user) and is readable
  only by that user's requests and the agent runtime. The UI only ever shows `@username`.
- Users can revoke EasyQuery at any time on GitHub under **Settings → Applications → Authorized OAuth Apps**.
- Each Copilot session runs in `mode="empty"` with only EasyQuery's tools available; any other permission request
  (shell, files, URLs, MCP) is rejected. Sessions are deleted after each call.
- The Copilot runtime is downloaded and checksum-verified when the image is built; containers never download code
  at run time (`COPILOT_SKIP_CLI_DOWNLOAD=1`).
