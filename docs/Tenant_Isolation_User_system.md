# Tenant Isolation & User System — Architecture and Dev Plan

*Status: proposal (2026-11-17). Extends MVP guardrail 2 (Tenant Isolation from Day 1) and the Phase 3
Connection Manager in [one_data_navigator_mvp_dev_plan.md](one_data_navigator_mvp_dev_plan.md).*

## 1. Goals

1. **Corporate clients own their users.** Every user belongs to exactly one client (tenant), e.g.
   Cotality (`cotality.com`) or Indulge (`indulgetheapp.com`). A tenant can have several admins, all on the
   tenant's own email domain (e.g. `kewan@cotality.com`).
2. **Per-client sign-in method.** SSO clients (Cotality: Google Workspace) sign in with SSO; clients without
   SSO yet (Indulge) sign in with their company email and a password.
3. **Store client and user profiles:** tenant name, domains, admins, auth method; user email, name, role,
   status, last sign-in.
4. **Sign-in asks for organization + company email**, then routes to the right method.
5. **Branding:** after sign-in the brand bar reads **"{tenant name} | Smart Data Navigator"**.
6. **Tenant isolation from day 1** (guardrail 2): every Postgres row, GCS path, cache key and Secret Manager
   entry is partitioned by `tenant_id`, which is resolved from the *verified token* in the API (never from
   client input) and enforced in depth by Postgres Row-Level Security.
7. **Connection Manager (Phase 3)** works inside this model: tenant admins manage shared connections, users
   manage personal ones, secrets stay tenant-bound.

## 2. Where we are today

| Area | Current state (release `42492db`) |
|---|---|
| Sign-in | Google OIDC only (authorization code + PKCE in the Next.js BFF, sealed `odn_session` cookie, 1-hour Google ID token, no refresh). |
| Tenant resolution | `TokenVerifier` (`backend/src/odn/tenancy/oidc.py`) maps Google's `hd` claim through the env var `ODN_OIDC_TENANT_DOMAINS` (`cotality.com=cotality`); accounts without a mapped `hd` go to `ODN_OIDC_DEFAULT_TENANT` (`datahop`). `odn_resolve_tenant(slug, issuer)` then looks up `tenants`. |
| Tenants | `tenants(id, slug, name, oidc_issuer)`; created with `python -m odn.db.provision tenant`. No domains, admins, auth method or status. |
| Users | Upserted on every request (`tenancy/context.py`), unique `(tenant_id, subject)`; `role ∈ member / data_lead / admin`, set only via `provision role`. No invited/disabled status, no email uniqueness. |
| Branding | Deployment-wide theme (`ODN_THEME`); the brand bar shows the theme's company name (`cotality`) for every tenant. |
| RLS | All 13 tenant tables have `FORCE ROW LEVEL SECURITY` keyed on `odn.tenant_id` (+ `odn.user_id` for personal rows), fail-closed when unset. `tenants` has no RLS but the app role cannot read it (security-definer lookup only). |
| Connections | Tenant-shared (`owner_user_id NULL`, Secret Manager refs `odn-<tenant_id>-*`, created by data leads/operators) and personal (owner-only via RLS, secrets AES-256-GCM in Postgres with AAD bound to tenant+owner+connection). Postgres + SSH tunnel profiles in Settings. |

## 3. Password sign-in for non-SSO clients: options

The question was how Indulge users can sign in with email and password. There are three realistic ways to do it:

| | A. **Google Cloud Identity Platform** (multi-tenant) | B. Self-hosted Keycloak on the VM | C. Build it in-house |
|---|---|---|---|
| What it is | Google's managed CIAM (the enterprise tier of Firebase Auth). One **Identity Platform tenant per client**, each with its own users and providers: email/password, Google, SAML, OIDC. | Open-source IdP; one realm (or org) per client. | Our own `password_hash` (argon2id) + reset emails + lockout + MFA. |
| Passwords stored by | Google | Us (Keycloak DB on the VM) | Us |
| Reset / verify emails | Built in (`sendOobCode`), templated per tenant | Built in (needs SMTP) | We build it (needs SMTP) |
| Brute-force, password policy, MFA | Built in (password policy, email-enumeration protection, TOTP/SMS MFA) | Built in | We build all of it |
| Later move a client to SSO | Add a SAML/OIDC provider to their IdP tenant, no app change | Add an IdP to the realm | New integration |
| Tokens our API verifies | RS256 JWT, `iss = https://securetoken.google.com/<project>`, `aud = <project>`, tenant in the `firebase.tenant` claim | RS256 JWT per realm | Our own JWT |
| Cost | Email/password: **free up to 50k MAU**; SAML/OIDC: 50 MAU free, then $0.015/MAU ([pricing](https://cloud.google.com/identity-platform/pricing)) | VM RAM (~1 GB) + ops (upgrades, backups) | Engineering time + security liability |
| Ops | None (managed, same GCP project) | Medium | High |

**Recommendation: A — Identity Platform**, used only for password tenants at first:
- We are already on GCP and Google sign-in; tokens are Google-signed JWTs that fit our existing
  verifier model (issuer + tenant claim), so the API change is small.
- We never store or hash passwords, and get reset/verification emails, lockout and MFA for free.
- An Indulge move to SSO later is a console change on their IdP tenant.
- Cotality keeps the current direct Google sign-in, which already works. Moving SSO tenants onto Identity
  Platform as well is optional later (one sign-in stack), not required.

**How Indulge sign-in works (server-side, no Firebase SDK in the browser):** the browser posts organization,
email and password to our BFF (same-origin, TLS). The BFF calls the Identity Platform REST API
`accounts:signInWithPassword` with the client's IdP `tenantId`, receives an ID token **and a refresh token**,
and stores both in the sealed session cookie. The password is never stored or logged by us. The API verifies
the ID token exactly like a Google token.

*To confirm in the spike (U0):* `firebase.tenant` claim name and JWKS URL; whether completing a password-reset
link sets `email_verified`; Firebase Auth emulator support for tenants (local dev); required IAM role for the
VM service account to create users (Identity Platform Admin).

## 4. Target architecture

```
                   ┌──────────────────────── Browser ─────────────────────────┐
                   │ Sign-in: Organization + Work email → (SSO redirect | password) │
                   └───────────────┬──────────────────────────────────────────┘
                                   │ same-origin
┌──────────────────────────────────▼───────────────────────────────────────────────┐
│ Next.js BFF                                                                       │
│  /auth/discover  → API /v1/auth/discover (public, rate-limited, generic errors)   │
│  /auth/login     → Google OIDC (+ hd, login_hint)          ─┐                     │
│  /auth/password  → Identity Platform signInWithPassword     ├→ sealed odn_session │
│  /auth/refresh   → refresh ID token before expiry           ─┘  (id + refresh tok)│
│  /api/odn/*      → API with Bearer ID token                                       │
└──────────────────────────────────┬───────────────────────────────────────────────┘
                                   │ Bearer token
┌──────────────────────────────────▼───────────────────────────────────────────────┐
│ API                                                                               │
│  TrustedIssuers: Google | Identity Platform | local mock IdP  → verify JWT        │
│  odn_resolve_identity(issuer, external_id) → tenant_id   (DB, not env)            │
│  checks: tenant active · email_verified · email domain ∈ tenant_domains           │
│          user not disabled · invited user linked by verified email                │
│  RequestContext(tenant_id, user_id, role) → tenant_transaction → RLS              │
└──────────────────────────────────────────────────────────────────────────────────┘
```

### 4.1 Data model (migration `0009_tenant_directory.sql`)

```sql
-- Tenant profile
ALTER TABLE tenants
  ADD COLUMN status      text NOT NULL DEFAULT 'active'  CHECK (status IN ('active', 'suspended')),
  ADD COLUMN auth_method text NOT NULL DEFAULT 'google'  CHECK (auth_method IN ('google', 'password', 'oidc', 'saml')),
  ADD COLUMN join_policy text NOT NULL DEFAULT 'invite'  CHECK (join_policy IN ('domain', 'invite')),
  ADD COLUMN theme       text NOT NULL DEFAULT 'default',
  ADD COLUMN updated_at  timestamptz NOT NULL DEFAULT now();

-- A domain belongs to exactly one tenant (global uniqueness = no cross-tenant email capture).
CREATE TABLE tenant_domains (
  domain      text PRIMARY KEY CHECK (domain = lower(domain)),
  tenant_id   uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
  verified_at timestamptz,             -- DNS TXT proof or operator attestation; unverified = unusable
  created_at  timestamptz NOT NULL DEFAULT now()
);

-- How a verified token maps to a tenant (replaces ODN_OIDC_TENANT_DOMAINS and tenants.oidc_issuer).
CREATE TABLE tenant_identity_providers (
  issuer      text NOT NULL,           -- https://accounts.google.com | https://securetoken.google.com/<project> | mock
  external_id text NOT NULL,           -- Google hd domain | Identity Platform tenant id | mock tenant claim
  tenant_id   uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
  PRIMARY KEY (issuer, external_id)
);

-- User profile
ALTER TABLE users
  ADD COLUMN issuer     text,          -- with subject: the identity; NULL while invited
  ADD COLUMN status     text NOT NULL DEFAULT 'active' CHECK (status IN ('invited', 'active', 'disabled')),
  ADD COLUMN invited_by uuid REFERENCES users (id) ON DELETE SET NULL,
  ADD COLUMN invited_at timestamptz,
  ALTER COLUMN subject DROP NOT NULL;
CREATE UNIQUE INDEX users_tenant_email ON users (tenant_id, lower(email));
CREATE UNIQUE INDEX users_identity     ON users (tenant_id, issuer, subject) WHERE subject IS NOT NULL;
```

- `tenant_domains` and `tenant_identity_providers` are **directory tables** like `tenants`: no app-role
  grants; the app reaches them only through `SECURITY DEFINER` functions:
  - `odn_resolve_identity(issuer, external_id) → tenant_id` (active tenants only)
  - `odn_discover_tenant(org text, email_domain text) → (slug, name, auth_method, idp_tenant_id)`; returns a
    row only when the domain is verified **and** belongs to the tenant named by `org` (slug or name,
    case-insensitive).
- **Admins are users with `role = 'admin'`**; a trigger rejects `admin` for an email whose domain is not one
  of the tenant's verified domains (the `datahop` platform tenant, which has no domains, is exempt).
  A second guard stops demoting/disabling the last active admin.
- Backfill: existing `tenants.oidc_issuer` + `ODN_OIDC_TENANT_DOMAINS` rows become
  `tenant_identity_providers` / `tenant_domains`; existing users get `issuer` from their tenant.
  `tenants.oidc_issuer` and the env mapping are removed one release later.

### 4.2 Sign-in flow

```mermaid
sequenceDiagram
  participant B as Browser
  participant F as BFF (Next.js)
  participant A as API
  participant G as Google OIDC
  participant I as Identity Platform
  B->>F: POST /auth/discover {organization, email}
  F->>A: POST /v1/auth/discover
  A-->>F: {method: google, hd} | {method: password} | generic "not found"
  alt SSO tenant (Cotality)
    F-->>B: redirect /auth/login?hint=email
    B->>G: authorize (PKCE, hd=cotality.com, login_hint)
    G-->>F: code → ID token (+ refresh token, access_type=offline)
  else Password tenant (Indulge)
    B->>F: POST /auth/password {organization, email, password}
    F->>A: discover again (server-side; never trust the client's tenant id)
    F->>I: accounts:signInWithPassword {email, password, tenantId}
    I-->>F: ID token + refresh token
  end
  F-->>B: sealed odn_session cookie
  B->>F: GET / (any API call)
  F->>A: Bearer ID token
  A->>A: verify JWT → issuer + (hd | firebase.tenant) → tenant_id (DB)
  A->>A: email_verified, domain ∈ tenant_domains, user not disabled
  A-->>F: data scoped by RLS
```

Rules:
- **The typed organization and email are routing hints only.** The tenant is always derived from the
  verified token (issuer + tenant claim) and cross-checked against the verified email's domain. A mismatch
  (e.g. a Cotality Google account answering an Indulge prompt) is rejected.
- **No enumeration:** `discover` returns one generic message for unknown org, unknown domain or mismatch,
  and is rate-limited per IP and per email (and Identity Platform's email-enumeration protection is on).
- **Joining:** `join_policy = domain` lets any verified `@domain` account in on first sign-in as `member`
  (good for Cotality); `invite` requires a row created by an admin (default; recommended for Indulge).
- **Invited users** (`status = invited`, no subject yet) are linked on first sign-in only if the token's
  email is verified and equal to the invite email.
- **Session refresh:** the sealed cookie keeps the refresh token; the BFF refreshes the ID token shortly
  before expiry (Google: `access_type=offline`; Identity Platform: `securetoken.googleapis.com/v1/token`).
  This also closes the current "session ends after 1 hour" hardening item.
- Disabled users and suspended tenants get 403 on every request (checked in `resolve_context`).
- The last organization used is remembered in a non-sensitive cookie to prefill the form.

### 4.3 Password tenant lifecycle (Indulge)

1. Operator: `provision tenant indulge --name Indulge --auth password --domain indulgetheapp.com
   --idp-tenant <id> --admin alice@indulgetheapp.com` (creates the Identity Platform tenant via its API,
   marks the domain verified after DNS TXT check or attestation, invites the first admin).
2. Invite: the API creates the user in the Identity Platform tenant (no password) and has Identity Platform
   send its **set-password email** (`sendOobCode PASSWORD_RESET`); our row is `status = invited`.
3. User sets a password via the emailed link, then signs in with organization + email + password.
4. Forgot password: "Forgot password?" on the sign-in form → same `sendOobCode` (generic response).
5. Admin disables a user: our row `status = disabled` (immediate 403) and the IdP account is disabled.

### 4.4 Roles

| Role | Scope | Can |
|---|---|---|
| Platform operator (datahop staff) | All tenants, via `provision` CLI on the VM (later an internal console) | Create/suspend tenants, verify domains, set auth method, invite first admin. **No data access by default.** |
| `admin` (tenant) | One tenant | Everything a data lead can, plus: users (invite, role, disable), shared connections, tenant profile/branding. Email must be on a tenant domain. |
| `data_lead` | One tenant | Curated definitions, pinned joins/metrics, shared connections (as today). |
| `member` | One tenant | Explore, chat, personal connections and LLM key. |

### 4.5 Branding

- `GET /v1/me` returns `{user: {id, email, name, role}, tenant: {slug, name, theme}}`.
- `page.tsx` (server component) fetches it with the session token and renders
  **`{tenant.name} | Smart Data Navigator`** (company slot = tenant name); the HTML title follows.
- Before sign-in, no tenant is known: the brand bar shows the platform brand and product name only.
- `tenants.theme` replaces the deployment-wide `ODN_THEME` after sign-in (Cotality keeps theme A;
  others use `default`). Pre-sign-in uses the platform default.

## 5. Tenant isolation (guardrail 2): status and gaps

| Surface | Today | Gap / action |
|---|---|---|
| Tenant resolution | From the verified token, never client input ✅ | Mapping lives in env (`ODN_OIDC_TENANT_DOMAINS`) → move to `tenant_identity_providers` (U1/U2). Add `email_verified` + domain cross-check for all issuers. |
| Postgres | `FORCE RLS` on all 13 tenant tables, fail-closed ✅; personal rows also keyed on `odn.user_id` ✅ | **Guard test:** enumerate every table in `public` and fail unless it has `FORCE RLS` or is an allow-listed directory table (`tenants`, `tenant_domains`, `tenant_identity_providers`), so a new table can't ship without RLS. Wire RLS isolation tests into CI. |
| GCS | Keys `odn/<tenant>/<user>/<session>/…` built from `RequestContext` ✅ | One bucket and a service account with access to all of it, so the only boundary is code. Add `assert_tenant_key(ctx, key)` before every read/write/sign, and read object keys only from RLS-protected rows. v2: per-tenant bucket or CMEK for enterprise tenants. |
| In-process caches | Search index keyed `(tenant_id, connection_id, allowlist)` ✅ | Agent attachment notes keyed `(session_id, url)` ❌ → prefix with `(tenant_id, user_id)`. Rule: **every cache key starts with `tenant_id`**; checked in review. |
| Secret Manager | Shared refs must match `projects/*/secrets/odn-<tenant_id>-*` ✅; personal secrets AES-256-GCM with AAD = tenant+owner+connection ✅ | Shared **SSH tunnel keys** and shared connection credentials entered in the UI need the same naming (see §6). VM service account access is project-wide, so the name check is the boundary; keep it covered by tests. |
| Agent sessions | ADK `user_id` = tenant+user ✅ | — |
| LLM budgets, audit, telemetry | Per tenant ✅ | Add `user.*`, `tenant.*` audit events (invites, role changes, sign-in failures). |
| DuckDB worker | Receives artifact keys from the API | Confirm keys come only from RLS rows (covered by the GCS assertion). |
| Local dev | Seeded `local` tenant can have an empty `oidc_issuer` ("tenant is not provisioned") | Seed writes `tenant_identity_providers` for the mock IdP (fixed by U1). |

## 6. Connection Manager (Phase 3) in the tenant model

| Item | Design |
|---|---|
| Who manages what | **Shared** connections: tenant `admin` / `data_lead`, visible to all users in the tenant (RLS). **Personal**: the owner only (as today). |
| Shared credentials entered in the UI | Today shared connections need an operator to put the secret in Secret Manager. Proposal: the API writes `odn-<tenant_id>-conn-<connection_id>` (service account and Postgres password) and `odn-<tenant_id>-ssh-<connection_id>` (tunnel private key). Requires granting the VM service account secret creation with an IAM condition limited to names starting with `odn-`. *Decision D3.* Fallback: encrypt shared secrets in Postgres like personal ones (AAD = tenant+connection). |
| SSH tunnel | Mandatory host-key pinning (done); keys per §5; **end-to-end test against a real bastion** (open Phase 3 item). Client allowlists the VM egress IP `136.71.108.203`. |
| Allowlist | `allowed_objects` stays per connection; admins edit it in the UI. |
| Audit | `connection.created/updated/deleted/tested` events with tenant and user. |
| Later (v2) | Per-connection grants to users/groups instead of "all tenant users". |

## 7. Dev plan

Estimates are focused engineering days. Each phase lists its exit check.

### U0 — Spike & decisions (2 days)
- [ ] Create an Identity Platform project config with multi-tenancy; tenant `indulge-test` with email/password.
- [ ] Server-side `accounts:signInWithPassword` with `tenantId` from the BFF; record exact token claims
      (`iss`, `aud`, `firebase.tenant`, `email_verified`), JWKS URL, refresh flow.
- [ ] Confirm the set-password (reset) email flow, `email_verified` after it, password policy and
      email-enumeration protection settings.
- [ ] Local dev: confirm the Firebase Auth emulator supports tenants (else: a test IdP tenant in a dev project).
- [ ] Close decisions D1–D5 (§8).
- **Exit:** a test user signs in through the BFF and the API logs its verified claims.

### U1 — Tenant directory & user profiles (3 days)
- [ ] Migration `0009_tenant_directory.sql` (§4.1) with backfill from `tenants.oidc_issuer` and
      `ODN_OIDC_TENANT_DOMAINS`; `SECURITY DEFINER` functions; admin-domain and last-admin triggers.
- [ ] `provision` CLI: `tenant` (+ `--auth`, `--domain`, `--idp-tenant`, `--theme`), `domain add/verify`,
      `invite`, `user disable`, `show` with domains/admins.
- [ ] RLS guard test (all tables forced or allow-listed) + cross-tenant tests for the new tables.
- [ ] Local seed writes the mock IdP mapping (fixes the empty-issuer seed problem).
- **Exit:** prod data migrated (Cotality via `cotality.com`, `datahop` default), tests green.

### U2 — Multi-issuer verification & context (3 days)
- [ ] `TokenVerifier` → `TrustedIssuers` (Google, Identity Platform, local mock), each with JWKS, audience
      and tenant-claim path (`hd`, `firebase.tenant`, mock claim; nested claim support).
- [ ] `resolve_context`: tenant via `odn_resolve_identity`; require `email_verified`; email domain ∈ tenant
      domains (except `datahop`); `join_policy`; link invited users by verified email; block disabled users
      and suspended tenants; keep the personal-Google → `datahop` path.
- [ ] `GET /v1/me` returns user + tenant profile.
- [ ] Tests per issuer with locally signed JWTs (wrong issuer, wrong tenant claim, unverified email,
      foreign domain, disabled user, invited linking).
- **Exit:** existing Cotality and datahop sign-ins unchanged in prod; new checks covered by tests.

### U3 — Sign-in UX & password sign-in (4 days)
- [ ] Sign-in card: **Organization** + **Work email** → `POST /v1/auth/discover` (public, rate-limited,
      generic errors) → SSO redirect (with `hd` + `login_hint`) or password step.
- [ ] BFF `/auth/password` (server-side Identity Platform call), "Forgot password?", set-password landing.
- [ ] Session cookie stores the refresh token; BFF refreshes ID tokens before expiry (Google + Identity
      Platform). Remember last organization.
- [ ] Rate limiting for discover/password (per IP and per email); no secrets or passwords in logs.
- **Exit:** a Cotality user signs in via SSO and an invited Indulge user via password, both on staging/local;
      sessions survive past 1 hour.

### U4 — Tenant admin console (3 days)
- [ ] Settings → **Organization** tab (admins): tenant profile, domains (read-only), users table (name,
      email, role, status, last sign-in), invite (domain-checked), change role, disable/enable, admins list.
- [ ] API `/v1/admin/users*`, `require_admin`; Identity Platform user create/disable for password tenants.
- [ ] Audit events `user.invited`, `user.role_changed`, `user.disabled`, `auth.failed`.
- **Exit:** an Indulge admin invites a colleague who sets a password and signs in; the last admin cannot be
      removed.

### U5 — Branding (1 day)
- [ ] Brand bar `{tenant name} | Smart Data Navigator` from `/v1/me`; page title; per-tenant `theme`.
- **Exit:** Cotality users see "Cotality | Smart Data Navigator", Indulge users "Indulge | Smart Data Navigator".

### U6 — Isolation hardening (2 days)
- [ ] `assert_tenant_key` for every GCS read/write/sign; attachment-note cache key gets tenant+user.
- [ ] CI: policy suite, RLS isolation + guard tests, lint, image builds (existing hardening item).
- **Exit:** a deliberately mis-scoped key or cache lookup fails a test.

### U7 — Connection Manager in the tenant model (3 days, Phase 3 remainder)
- [ ] Shared connections created/edited in the UI by admins/data leads; secrets per D3
      (`odn-<tenant_id>-conn-*`, `odn-<tenant_id>-ssh-*`).
- [ ] SSH tunnel end-to-end test against a real bastion; Test connection shows tunnel steps.
- [ ] Connection audit events; allowlist editing in the UI.
- **Exit:** Phase 3 exit check's SSH-tunnel Postgres case passes on the hosted URL.

**Total ≈ 21 days.** U1→U2→U3 is the critical path; U5 can ship right after U2; U6 and U7 can run in
parallel with U3/U4.

### Rollout
1. Ship U1+U2 behind the existing Google flow (no visible change); verify prod sign-ins.
2. Ship U3+U5: new sign-in card; Cotality via SSO; create the Indulge tenant and first admin.
3. Ship U4; Indulge admin invites users.
4. Remove `ODN_OIDC_TENANT_DOMAINS` / `tenants.oidc_issuer` after one release.

## 8. Open decisions

| # | Decision | Recommendation |
|---|---|---|
| D1 | Password IdP | Identity Platform (§3). |
| D2 | Default join policy | `domain` for SSO tenants, `invite` for password tenants. |
| D3 | Shared connection secrets | Secret Manager with name-conditioned create rights; fallback Postgres encryption. |
| D4 | Ask for organization at all? | Keep it (requested), but also infer from the email domain and prefill; both must agree. |
| D5 | MFA for password tenants | Optional per tenant at first (TOTP); required for admins. |
| D6 | Move SSO tenants onto Identity Platform | Later, only if we want one sign-in stack or a client brings SAML (Okta/Entra). |
