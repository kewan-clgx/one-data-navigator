# Phase 5: Collibra Catalog Integration — Plan & Tracker

**Status:** Option A chosen (2026-11-19) and built in QueryMuse (`/v1/collibra/*`, agent tools, JWKS); **waiting on Cotality's Collibra admin** to trust QueryMuse's token issuer ([§4.2](#42-collibra-admin-setup-one-time-per-environment)).
**Owner:** QueryMuse team · **First client:** Cotality (`cotality` tenant) · **Updated:** 2026-11-19
**Parent plan:** [one_data_navigator_mvp_dev_plan.md → Phase 5](one_data_navigator_mvp_dev_plan.md#phase-5-collibra-catalog-integration-post-v1-not-yet-scheduled)

## 1. Goal

Let users and the agent search the Collibra data catalog (PRD and INT) for business definitions, field
mappings and lineage, so catalog tasks such as "find the best Collibra field for each requested field name"
are answered from Collibra evidence instead of the agent's own guesses.

**Why (2026-11-17):** a user attached a five-field mapping template (business name, business type, corporate
revenue, location sales, employee numbers) asking for a Collibra search across PRD and INT. With no catalog
tool, the agent mapped the fields against the allowlisted BigQuery schemas only and presented invented
definitions, confidence scores and environments as catalog facts. The agent now says when a system is
unavailable and labels its own matches as suggestions; this phase adds the real capability.

## 2. Findings (2026-11-19)

1. **QueryMuse sign-in does not give Collibra access.** Cotality users sign in to QueryMuse with Google OIDC
   (`cotality.com`). Collibra (`https://corelogic.collibra.com`) has its own SSO session in the user's browser;
   nothing in the QueryMuse session is accepted by Collibra today.
2. **The existing integrations are desktop-only.**
   - Collibra connector (`idap_agents_glb-ag_foundation-collibra_connector`, Node CLI): opens a visible
     browser for SSO/MFA, hands the session cookies to a local, memory-only HTTP client, serves bounded
     same-origin REST GETs over a current-user-only socket. No cookies leave the machine by design.
   - KWAgent stdio MCP wrapper (`docs/Collibra_MCP_wrapper.md`, branch `sdap-data-navigator`) and the
     `navigator_docker_env` Collibra tab call that CLI; disabled in Docker (`COLLIBRA_ENABLED=false`).
   - Neither can run on the hosted server: there is no browser for the user's MFA, and moving SSO cookies to a
     server is what the connector forbids. The wrapper doc: *"A login on the user's Mac does not authenticate
     the deployed agent."*
3. **Collibra REST API authentication** ([developer docs](https://developer.collibra.com/tutorials/collibra-json-web-token-authentication-guide.md)):
   - Basic auth (username/password) — SSO users have no Collibra password.
   - OAuth 2.0 **client credentials only** — app identity; no authorization-code flow, so no per-user
     "Connect Collibra" button.
   - Session login (`JSESSIONID`) — same password problem.
   - **JWT bearer** — Collibra validates a signed JWT against one configured JWKS URL / issuer / audience and
     maps a principal claim (default `sub`) to a Collibra username. Environment-wide setting in Collibra
     Console; documented for client applications (service accounts), not explicitly for forwarded user tokens.
4. **Layers stay separate** (wrapper doc): app login, Collibra-service authentication and Collibra
   authentication are distinct; one token must not be assumed valid for all three. With a shared identity,
   answers must not be presented as the user's own Collibra entitlements.

## 3. Hosted architecture (target)

```
Browser ── QueryMuse BFF (session) ── API /v1/collibra/* ── CollibraClient ──HTTPS──▶ Collibra (PRD / INT)
                                          ▲                        │ credential provider (A or B, §4)
ADK agent tools (collibra_*) ─────────────┘                        └─ per-tenant config, Secret Manager
```

- Read-only Collibra REST client **in the API** (not the agent), so tenant/user context, audit, rate limits and
  credential access stay server-side; the agent calls it through `/v1/collibra/*` like other tools.
- Configured **per tenant and per environment** (PRD, INT): base URL, auth mode, credential reference.
- No catalog content shared across tenants or users (cache keys include tenant and, for option A, user).

## 4. Authentication: option A, QueryMuse-issued per-user tokens (decided 2026-11-19)

No service account is available, so QueryMuse calls Collibra **as the signed-in user**. Users sign in once
(to QueryMuse); they never sign in to Collibra inside the app, and their own Collibra permissions apply.

### 4.1 Design

Instead of forwarding the user's Google token, **QueryMuse signs its own short-lived token for Collibra**
(Collibra's JWT bearer authentication), after its own checks:

```
User ─(Google SSO / password)─▶ QueryMuse API: verified token → tenant, active user, verified-domain email
      API mints JWT {iss: https://app.querymuse.ai, aud: <env audience>, sub: <work email>, exp: +5 min} (RS256)
      API ──Authorization: Bearer <JWT>──▶ Collibra ── verifies via https://app.querymuse.ai/.well-known/jwks.json
                                                   └─ maps sub → Collibra user → that user's permissions
```

Why this beats forwarding Google ID tokens:
- Google tokens never leave QueryMuse; the Collibra token is useless against our API (different issuer and audience).
- Works for every sign-in method (Google SSO, Identity Platform password, future Okta/Entra), one issuer for Collibra.
- QueryMuse enforces what Collibra cannot: tokens are minted only for **active users** whose email is **verified and
  on the organization's verified domains**, and only for Collibra environments configured for **that organization**.
- 5-minute lifetime, audience bound per environment, `jti` per token; key in Secret Manager.

Implementation: `odn/platform/collibra/token.py` (key, JWKS, mint), `client.py` (bounded REST GETs),
`api/collibra_routes.py`, `agent/collibra_tools.py`, table `collibra_environments` (migration `0015`, RLS),
`provision collibra`, JWKS published by the frontend route `/.well-known/jwks.json`.
Prod key: Secret Manager `odn-collibra-signing-key` (RSA 3072), `ODN_COLLIBRA_SIGNING_KEY_SECRET`.

### 4.2 Collibra admin setup (one time, per environment)

Collibra Console → Infrastructure → environment → Data Governance Center service → Configuration → Edit → **JWT**:

| Setting | Value |
|---|---|
| JSON Web Key Set URL | `https://app.querymuse.ai/.well-known/jwks.json` |
| JWT Token Types | `jwt` (default `at+jwt,jwt` also works) |
| JWT Algorithms | `RS256` |
| JWT Issuer | `https://app.querymuse.ai` |
| JWT Audience | e.g. `querymuse-collibra-prd` (one per environment; must match QueryMuse's config) |
| JWT Principal ID Claim Name | `sub` (default) — QueryMuse puts the user's work email there (or the part before `@`) |

Save and restart the environment. Then the operator connects the tenant:

```sh
deploy/deploy.sh provision collibra cotality prd --url https://corelogic.collibra.com --audience querymuse-collibra-prd
# --principal email_local_part if Collibra usernames are not full emails
```

Users check it with the agent ("is Collibra connected?") or `GET /v1/collibra/prd/status`.

**Caveats to raise with Cotality:** Collibra has one JWT configuration per environment; if JWT auth is already used
for another issuer, it must be coordinated (e.g. a combined JWKS). Trusting QueryMuse's issuer means QueryMuse can
act as Cotality Collibra users who sign in to QueryMuse; mitigations above, plus logs of every Collibra call
(user, environment, operation) and key rotation via a new secret version.

### 4.3 Alternative kept for later

**B. Tenant service account** (OAuth client credentials) — no Collibra-side JWT change, but everyone sees the
service account's view. Can be added as a second credential provider if Cotality prefers it.

### Open questions for Cotality's Collibra / platform owners

- [ ] Approve QueryMuse as a JWT issuer for Collibra (security review).
- [ ] Is Collibra JWT authentication already configured (JWKS / issuer in use)?
- [ ] Collibra username format — equal to the work email?
- [ ] Audience value per environment, PRD and INT base URLs (known: `https://corelogic.collibra.com`), rate limits.
- [ ] Data-handling rules for catalog metadata sent to the LLM.

## 5. Work breakdown

### 5.1 Connector & access (API)
- [x] `CollibraClient` (httpx): bounded same-origin REST GETs only — asset search, asset, attributes,
      relations, field inventory; timeouts, retries on 429/5xx with backoff, `limit` 1–100, no mutation or GraphQL.
- [x] Credential provider: QueryMuse-signed per-user JWT (A); service account (B) can be added behind the same client.
- [x] Tenant config: migration for `collibra_environments` (tenant_id, env, base_url, auth_mode, secret_ref),
      RLS ENABLE+FORCE and grants (guard test `tests/db/test_rls.py`).
- [x] Endpoints: `/v1/collibra/environments`, `/{env}/status`, `/{env}/assets?q=`, `/{env}/assets/{id}`, `/attributes`,
      `/relations`, `/fields`, `POST /{env}/describe`; per-user rate limit (120/min); every call logged (user, env, op).
- [x] Provision CLI: `provision collibra <tenant> <env> --url … --auth service|jwt --secret-ref …`.

### 5.2 Agent tools & field-mapping workflow
Port the KWAgent wrapper's tool set:
- [x] `collibra_search_assets` (text, optional domain, `ANYWHERE`/`START`/`EXACT`, limit/offset, environment)
- [x] `collibra_environments`, `collibra_get_relations` (incoming/outgoing); single asset/attributes via describe
- [x] `collibra_describe_assets` (1–20 ids, per-asset errors)
- [x] `collibra_get_api_fields` (field inventory with continuation; nested containers listed, not yet expanded)
- [ ] Field mapping: for each requested field (e.g. rows of an uploaded template) search Collibra, prefer
      BigQuery-backed assets, return best match + runner-ups with evidence (asset link, definition,
      dataset · project · environment); heuristic scores labelled; unmatched fields labelled; result saved as
      a workspace table downloadable from Artifacts.
- [ ] Cross-check matched columns against the user's connections: confirm schema when allowlisted, else
      "cataloged, not queryable here".
- [x] Agent instructions: cite Collibra assets for every definition; never invent; say when Collibra is not
      connected; with option B, say answers reflect the shared catalog view.

### 5.3 UI
- [ ] **Collibra** side-panel tab: status, search, asset detail; selecting an asset scopes the chat.
- [ ] Explorer dictionary shows Collibra definitions with provenance ("Collibra · PRD").
- [ ] Settings / Organization (admins): connect and test Collibra per environment.

### 5.4 Security & tests
- [x] Credentials only via Secret Manager / encrypted store; never in prompts, logs or tool output.
- [x] Tests: client bounds and error mapping (401/403/429/timeout/invalid JSON) with sanitized fixtures;
      tenant isolation of config and caches; agent tool contracts.
- [ ] Security review of the chosen auth mode (Phase 4 review scope).

## 6. Exit check

The 2026-11-17 mapping template (business name, business type, corporate revenue, location sales, employee
numbers) is completed for PRD and INT with a Collibra asset link, definition and dataset · project ·
environment for every match; every definition traces to a Collibra asset, unmatched fields are labelled,
and nothing is invented.

## 7. Log

| Date | Note |
|---|---|
| 2026-11-17 | Phase 5 added after the invented-definitions incident. |
| 2026-11-19 | Auth research: SSO session not reusable on the server; options A (JWT) / B (service account); KWAgent wrapper design reviewed; tracker created. |
| 2026-11-19 | Option A chosen (no service account available). Built: QueryMuse-signed per-user tokens + JWKS, `collibra_environments` (RLS), `/v1/collibra/*`, five agent tools, provision CLI, tests against a fake Collibra that verifies our JWKS. Prod signing key created. Next: Collibra admin JWT setup, then live test and the Collibra UI tab. |
