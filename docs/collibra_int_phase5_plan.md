# Phase 5: Collibra Catalog Integration — Plan & Tracker

**Status:** Research done; **blocked on the authentication decision** (see [§4](#4-authentication-decision-blocking)).
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

## 4. Authentication decision (blocking)

| | **A. Per-user single sign-on (JWT bearer)** | **B. Tenant service account (OAuth client credentials)** |
|---|---|---|
| User experience | No extra login; uses the QueryMuse Google session | No extra login; admin connects once |
| Permissions | Each user's own Collibra permissions | Whatever the service account can see, for everyone |
| Collibra setup | Admin enables JWT: JWKS `https://www.googleapis.com/oauth2/v3/certs`, issuer `https://accounts.google.com`, audience = QueryMuse OAuth client ID, principal claim `email` (usernames must match) | Admin registers a read-only OAuth app; client ID/secret into Secret Manager `odn-<tenant>-collibra-<env>` |
| QueryMuse work | Forward the user's Google ID token (session already holds and refreshes it) | Token fetch + cache per tenant/env; log the asking user |
| Risks | One JWT config per environment (may clash with an existing one); needs Cotality security approval; proof test required (user tokens not explicitly documented) | Over-broad visibility; answers must be labelled as service-account view |
| Effort | Small in QueryMuse, gated on Collibra admin change | Small; fastest to ship |

**Recommended path:** B first (unblocks the catalog features), designed so A can replace the credential
provider later without changing tools or UI.

- [ ] Decision recorded (A, B, or B → A) with Cotality platform/Collibra owner approval.

### Open questions for Cotality's Collibra / platform owners

- [ ] Which identity provider fronts Collibra SSO (Okta, Entra ID, Google)?
- [ ] Is Collibra JWT authentication already configured (JWKS / issuer in use)?
- [ ] Collibra username format — equal to the work email?
- [ ] Is a read-only OAuth application (client credentials) allowed, and which permissions/communities may it read?
- [ ] PRD and INT base URLs (known: `https://corelogic.collibra.com`), and rate limits.
- [ ] Data-handling rules for catalog metadata sent to the LLM.

## 5. Work breakdown

### 5.1 Connector & access (API)
- [ ] `CollibraClient` (httpx): bounded same-origin REST GETs only — asset search, asset, attributes,
      relations, field inventory; timeouts, retries on 429/5xx with backoff, `limit` 1–100, no mutation or GraphQL.
- [ ] Credential providers behind one interface: `ServiceAccountProvider` (B) and `UserJwtProvider` (A).
- [ ] Tenant config: migration for `collibra_environments` (tenant_id, env, base_url, auth_mode, secret_ref),
      RLS ENABLE+FORCE and grants (guard test `tests/db/test_rls.py`).
- [ ] Endpoints: `GET /v1/collibra/status`, `/search`, `/assets/{id}`, `/assets/{id}/attributes`,
      `/assets/{id}/relations`, `/assets/describe`, `/api-fields/{id}`; per-user rate limit; audit log entries.
- [ ] Provision CLI: `provision collibra <tenant> <env> --url … --auth service|jwt --secret-ref …`.

### 5.2 Agent tools & field-mapping workflow
Port the KWAgent wrapper's tool set:
- [ ] `collibra_search_assets` (text, optional domain, `ANYWHERE`/`START`/`EXACT`, limit/offset, environment)
- [ ] `collibra_get_asset`, `collibra_get_attributes`, `collibra_get_relations` (incoming/outgoing)
- [ ] `collibra_describe_assets` (1–20 ids, per-asset errors)
- [ ] `collibra_get_api_fields` (field inventory with continuation, depth 0–2)
- [ ] Field mapping: for each requested field (e.g. rows of an uploaded template) search Collibra, prefer
      BigQuery-backed assets, return best match + runner-ups with evidence (asset link, definition,
      dataset · project · environment); heuristic scores labelled; unmatched fields labelled; result saved as
      a workspace table downloadable from Artifacts.
- [ ] Cross-check matched columns against the user's connections: confirm schema when allowlisted, else
      "cataloged, not queryable here".
- [ ] Agent instructions: cite Collibra assets for every definition; never invent; say when Collibra is not
      connected; with option B, say answers reflect the shared catalog view.

### 5.3 UI
- [ ] **Collibra** side-panel tab: status, search, asset detail; selecting an asset scopes the chat.
- [ ] Explorer dictionary shows Collibra definitions with provenance ("Collibra · PRD").
- [ ] Settings / Organization (admins): connect and test Collibra per environment.

### 5.4 Security & tests
- [ ] Credentials only via Secret Manager / encrypted store; never in prompts, logs or tool output.
- [ ] Tests: client bounds and error mapping (401/403/429/timeout/invalid JSON) with sanitized fixtures;
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
