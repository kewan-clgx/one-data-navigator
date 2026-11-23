# Plan: move production from querymuse.ai to easyquery.ai

**Status:** evaluation / checklist, not started (2026-11-23).
**Goal:** clients land on the app at the **apex `https://easyquery.ai`** (no `app.` prefix); `www.easyquery.ai` and
`app.easyquery.ai` redirect to it; public wall at `insights.easyquery.ai`; same VM (`136.71.108.203`). Every existing
`querymuse.ai` link, bookmark and email keeps working through permanent redirects.

**Short version:** the app is driven by `ODN_HOSTNAME` (app origin, email links, sign-in callback, same-origin check,
Collibra token issuer, link previews), so the code side is one `.env` change plus a few hard-coded strings. Most of
the work and lead time is **outside the repo**: DNS, Google sign-in (OAuth client + consent screen + domain
verification), Identity Platform, email (Resend, support mailbox) and the Collibra browser extension.

---

## 1. What's already in place (checked 2026-11-23)

| Record | Today | Needed |
|---|---|---|
| `easyquery.ai` (apex) | A → `136.71.108.203` ✅ | keep — **the app** |
| `www.easyquery.ai` | CNAME → `easyquery.ai` (→ VM) ✅ | keep — redirects to the apex |
| `app.easyquery.ai` | **missing** | A → `136.71.108.203` (optional: people will type it; redirects to the apex) |
| `insights.easyquery.ai` | **missing** | A → `136.71.108.203` |
| MX / SPF / DKIM / DMARC | **none** | only if we send or receive mail on easyquery.ai (§4.4) |
| CAA | none (any CA allowed, Let's Encrypt OK) | — |
| DNS host | GoDaddy (`ns37/38.domaincontrol.com`) | — |

Caddy gets Let's Encrypt certificates automatically for every configured hostname once its DNS points at the VM
(ports 80/443 already open). Add the records **at least an hour before** the switch so certificates issue on the
first request.

## 2a. The app at the apex: what changes

| Topic | Today (querymuse) | With the app at `easyquery.ai` |
|---|---|---|
| Landing page | `www.querymuse.ai` = marketing home (`/site`) + `/privacy`; app at `app.` | One host. A signed-out visitor at `https://easyquery.ai` sees the app's front page — today the sign-in card with the About slides |
| Website content | `/site` page served only on the site host | **Decided (2026-11-23): a separate marketing page on the same host.** `easyquery.ai/` shows the marketing landing page to signed-out visitors and the app to signed-in users; sign-in moves to `easyquery.ai/signin`; marketing pages live at paths (`/privacy`, `/terms`, later `/pricing`, `/about`, `/blog`). See §2b |
| Privacy policy | `www.querymuse.ai/privacy` | `https://easyquery.ai/privacy` (the app already has the `/privacy` route) |
| Google brand verification | Home `www.querymuse.ai`, privacy on it | Home `https://easyquery.ai`, privacy `https://easyquery.ai/privacy`. Google requires the home page to describe the app and **link to the privacy policy** — the sign-in page needs that footer link (small change, §5) |
| `www` | the website | 301 → apex |
| Cookies | host-only on `app.querymuse.ai` | host-only on `easyquery.ai` (no `Domain=` attribute, so they are **not** sent to `insights.` or other subdomains — keep it that way) |
| Public wall | `insights.querymuse.ai` | `insights.easyquery.ai` (unchanged pattern). Option: drop the subdomain and use `easyquery.ai/insights` — one certificate fewer, but the wall then shares the app's origin; keep the subdomain unless there's a reason |
| DNS | `app` A record | Apex A record (already there). An apex can't be a CNAME — fine, it's an A record |

## 2b. Marketing page: where it lands

| Option | Marketing | App | Verdict |
|---|---|---|---|
| **A. One host, smart root** | `easyquery.ai/` when signed out (+ `/privacy`, `/terms`, `/pricing`…) | `easyquery.ai/` when signed in; sign-in at `/signin` | **Chosen** — clients bookmark and type one address; Google's home page = the marketing page with the privacy link; one certificate, one cookie origin |
| B. App at apex, marketing at `www.` | `www.easyquery.ai` | `easyquery.ai` | Confusing: `www` and the bare domain show different things, and most people treat them as the same |
| C. Marketing at apex, app at `app.` | `easyquery.ai` | `app.easyquery.ai` | The common SaaS pattern, but not what we want (clients should land on the app at the bare domain) |

How A works:

- `/` checks the session (as `app/page.tsx` already does): signed in → the app (unchanged); signed out → the
  marketing landing page (today's `/site` content, upgraded), with **Sign in** and **Get started** buttons.
- `/signin` = today's sign-in card (organization + email, Google, personal accounts). Every "sign in" redirect in the
  app (`odn_return` share flow, Ask a follow-up, expired session) goes to `/signin` instead of `/`; after sign-in →
  back to the return path or `/`.
- Marketing routes are plain public pages (no session needed); the BFF and `/api/*`, `/auth/*`, `/s/*` are unchanged.
  If marketing later wants its own tool (Webflow, Framer, a separate Next app), Caddy can route those **paths**
  (`/`, `/pricing`, `/blog/*` for signed-out traffic) to it without changing the domain — the app keeps everything
  else.
- `www.easyquery.ai` and `app.easyquery.ai` → 301 to `easyquery.ai` (path kept).
- Effort ≈ 0.5–1 day (routing + `/signin` + redirects + landing page polish); can ship on querymuse first.

## 2. How the domain is wired today

| Where | Setting / code | Effect of the move |
|---|---|---|
| `/opt/odn/shared/.env` on the VM | `ODN_HOSTNAME=app.querymuse.ai`, `ODN_SITE_HOSTNAME=www.querymuse.ai`, `ODN_INSIGHTS_HOSTNAME=insights.querymuse.ai`, `ODN_REDIRECT_HOSTS=querymuse.ai` | Change to the easyquery names (§3) |
| `docker-compose.prod.yml` | `ODN_PUBLIC_URL=https://${ODN_HOSTNAME}`; `ODN_MAIL_FROM` default `QueryMuse <noreply@querymuse.ai>` | Public URL follows automatically; mail sender is a decision (§4.4) |
| `deploy/Caddyfile` | Blocks for app, site, insights, redirect hosts | Add explicit **old → new** redirect blocks that keep the path (§3) |
| BFF (`frontend/app/api/odn/[...path]/route.ts`, `lib/auth.ts sameOrigin`) | Writes must come from `ODN_PUBLIC_URL`'s origin | Only one app origin can serve; the old app host must **redirect**, not serve |
| Sign-in (`/auth/callback`) | Google redirect URI = `https://<ODN_HOSTNAME>/auth/callback` | Register the new URI before switching (§4.1) |
| Session cookie | Host-only cookie on the app host | Everyone signs in again once on the new domain (expected) |
| Browser storage | Active conversation, panel/display prefs in `localStorage` (per origin) | Reset once; conversations themselves are server-side and stay |
| Share links / previews | `og:image` etc. built from `ODN_PUBLIC_URL` (`metadataBase`) | New shares use the new domain; old links redirect |
| Emails (invite, reset, share) | Links from `ODN_PUBLIC_URL` (`/auth/action`) | New domain automatically; sender address per §4.4 |
| Collibra token mode | Token `iss` and JWKS at `https://<ODN_HOSTNAME>/.well-known/jwks.json` (`platform/collibra/token.py`) | Collibra's trust config must be updated if it was set up (§4.5) |
| Collibra browser extension | `manifest.json` matches + `policy.js APP_ORIGINS = ["https://app.querymuse.ai", …]` | New extension version needed (§4.5) |
| Hard-coded text | `SiteFrame.tsx SUPPORT_EMAIL`, `privacy/page.tsx`, `auth_emails.py` footer, `config.py mail_from/mail_reply_to`, "insights.querymuse.ai" in `Organization.tsx` and `ShareDialog.tsx`, README / docs | Replace or derive from config (§5) |
| Tenant routing | Email domains (`cotality.com`, `indulgetheapp.com` …) and IdP mappings | **Unaffected** (not our domain) |
| GCP (VM, static IP, firewall, buckets, Secret Manager) | — | **Unaffected** |

## 3. Target configuration

`.env` (VM; back up first as `.env.bak-<timestamp>`, apply with `deploy/deploy.sh env <file>`):

```dotenv
ODN_HOSTNAME=easyquery.ai
# No separate website host: the apex is the app and its signed-out page is the landing page (§2a).
ODN_SITE_HOSTNAME=
ODN_INSIGHTS_HOSTNAME=insights.easyquery.ai
# All go to the app (ODN_REDIRECT_TARGET falls back to ODN_HOSTNAME when there's no site host).
ODN_REDIRECT_HOSTS=www.easyquery.ai,app.easyquery.ai,querymuse.ai,www.querymuse.ai
# New: old app/wall hosts that must keep their paths (/s/<id>, /auth/action?…, /insights …)
ODN_LEGACY_APP_HOSTS=app.querymuse.ai
ODN_LEGACY_INSIGHTS_HOSTS=insights.querymuse.ai
# Decide in §4.4 (keep querymuse until easyquery mail is verified):
ODN_MAIL_FROM=QueryMuse <noreply@querymuse.ai>
```

Caddyfile additions (small code change):

```caddyfile
# Old app host → new app host, path and query kept (share links, set-password links, bookmarks).
{$ODN_LEGACY_APP_HOSTS:legacy-app.localhost} {
	redir https://{$ODN_HOSTNAME}{uri} permanent
}
{$ODN_LEGACY_INSIGHTS_HOSTS:legacy-insights.localhost} {
	redir https://{$ODN_INSIGHTS_HOSTNAME}{uri} permanent
}
```

(With no site host, `ODN_REDIRECT_HOSTS` → `ODN_REDIRECT_TARGET` = the app, path kept, so `www.easyquery.ai/x`
→ `easyquery.ai/x`. The dedicated legacy blocks make the old app/wall hosts explicit. With `ODN_SITE_HOSTNAME`
empty, the site block falls back to `site.localhost` and is inert.) Keep the `querymuse.ai` DNS records and these redirects **for at least 12 months**: share links in
Teams/Slack, set-password emails (valid for days) and Collibra extension installs point at the old host.

Note: `/auth/callback` on the old host must not be used for sign-in after the switch — it would redirect to the new
host without the matching session cookie and fail; that's why the Google redirect URI changes first (§4.1).

## 4. External services (the real work)

### 4.1 Google sign-in (Cotality, personal Google accounts) — critical path

Google Cloud console, project with the OAuth client (`ODN_OIDC_CLIENT_ID`):

1. **Verify domain ownership:** Google Search Console → add property `easyquery.ai` (DNS TXT record at GoDaddy).
2. **OAuth consent screen (Google Auth Platform → Branding):** add `easyquery.ai` to *Authorized domains*; update
   home page `https://easyquery.ai`, privacy `https://easyquery.ai/privacy` (and terms when we have one);
   app name if the product is renamed (§6). Changing these triggers **brand re-verification** (days, sometimes a
   week+); start early. Basic scopes (openid/email/profile) keep working meanwhile, but the consent screen may show
   the domain/name unverified until approved.
3. **OAuth client (Clients → Web application):** *add* (don't replace yet) redirect URI
   `https://easyquery.ai/auth/callback` and JavaScript origin `https://easyquery.ai`. Remove the querymuse
   entries only after the switch has settled.
4. Cotality: their Google admin sees the app by client ID — no action unless they restricted it by name/domain;
   tell them the new address.

### 4.2 Google Identity Platform (Indulge, personal email/password accounts)

- Identity Platform → Settings → **Authorized domains**: add `easyquery.ai` (and keep `app.querymuse.ai` for a
  while). Needed for its own action links / continue URLs.
- If Identity Platform's built-in emails are used as a fallback (no Resend), update the email templates' action URL
  to `https://easyquery.ai/auth/action`.

### 4.3 Microsoft sign-in (if built first — docs/Tenant_login_microsoft_plan.md)

Register redirect `https://easyquery.ai/auth/callback/microsoft` and publisher domain `easyquery.ai` from the
start (publisher verification needs the Partner Center email domain to match — plan which domain the company uses).

### 4.4 Email

| Need | Option A (fastest): keep querymuse mail | Option B: move mail to easyquery.ai |
|---|---|---|
| Invite / reset / share emails (Resend) | Keep `noreply@querymuse.ai` (verified, deliverable); links already point at easyquery | Add `easyquery.ai` in Resend; publish its SPF/DKIM (TXT) + return-path records at GoDaddy; add DMARC (`_dmarc` TXT, start `p=none`); set `ODN_MAIL_FROM=EasyQuery <noreply@easyquery.ai>` once Resend shows *Verified* |
| Support mailbox | Keep `support@querymuse.ai` (Microsoft 365 via GoDaddy) | Add `easyquery.ai` as a domain in the Microsoft 365 tenant (GoDaddy panel), MX + SPF + autodiscover records, alias `support@easyquery.ai` on the same mailbox |
| Text in app/emails | unchanged | `SUPPORT_EMAIL`, `mail_reply_to`, email footer → easyquery |

Recommendation: switch the **web domain first with option A**, then do option B and flip the sender — a mismatched
sender domain is fine for deliverability as long as it's the verified one.

### 4.5 Collibra

- **Browser extension (`frontend/extension/collibra-bridge`):** add `https://easyquery.ai/*` to `manifest.json`
  `content_scripts.matches` and `policy.js APP_ORIGINS` (keep the querymuse origin during the transition), bump the
  version (e.g. 0.3.0), rebuild the download zip. Users must install/reload the new version **before** the switch, or
  Collibra browser mode stops working on the new domain. If it's in the Chrome Web Store, publish the update
  (review can take days) and update the store listing's site/privacy URLs.
- **Token mode:** if Cotality's Collibra admin already trusts our JWKS, give them the new issuer
  `https://easyquery.ai` and JWKS URL `https://easyquery.ai/.well-known/jwks.json` (the old URL redirects,
  but the token `iss` changes and must match their config). Same signing key — no key rotation needed.

### 4.6 Other

- **Teams / Slack:** nothing to configure; old links redirect, new shares use easyquery. Slack/Teams cache previews
  per URL, so old cards don't change.
- **Search / SEO:** 301s handle it; optionally add `easyquery.ai` in Search Console and use *Change of address*
  from the querymuse property.
- **Monitoring / uptime checks, bookmarks in docs, client onboarding material:** update URLs.

## 5. Code changes (≈ 0.5 day) — ✅ done 2026-11-23

Built: smart root (`/` = marketing page `SiteHome` when signed out, the app when signed in; `/signin` = sign-in;
all sign-in redirects, sign-out and expired sessions go to `/signin`; old `/?account=personal` → `/signin?…`);
`/terms`; footer Privacy · Terms · support on the website and sign-in page; `lib/site.ts` (`ODN_SUPPORT_EMAIL`,
wall address from `ODN_INSIGHTS_HOSTNAME`, app host from `ODN_PUBLIC_URL`) replacing hard-coded querymuse text;
email footer / Reply-To from `ODN_SUPPORT_EMAIL`; Caddy `ODN_LEGACY_APP_HOSTS` / `ODN_LEGACY_INSIGHTS_HOSTS`
redirect blocks (path kept; `caddy validate` passes with the easyquery values — **multiple hosts must be separated
by ", "**); Collibra extension 0.3.0 (allows `https://easyquery.ai`); `deploy/.env.prod.example` shows the
easyquery values. Original checklist:

1. Caddyfile: the two legacy redirect blocks (§3); compose passes `ODN_LEGACY_*` to Caddy. Check that an empty
   `ODN_SITE_HOSTNAME` really falls back (compose default `site.localhost`) and `ODN_REDIRECT_TARGET` resolves to
   `ODN_HOSTNAME`.
1b. Smart root (§2b): signed-out `/` = marketing landing page (footer *Privacy · Terms · support@…*, needed for
   Google brand verification), sign-in at `/signin`, all sign-in redirects updated; `/terms` page.
2. Derive the public wall host from config instead of the hard-coded "insights.querymuse.ai" (`Organization.tsx`,
   `ShareDialog.tsx`) — e.g. a `NEXT_PUBLIC`-free server prop from `ODN_INSIGHTS_HOSTNAME`.
3. Support email / reply-to / email footer from settings (`ODN_SUPPORT_EMAIL`), not literals
   (`SiteFrame.tsx`, `config.py`, `auth_emails.py`, `privacy/page.tsx`).
4. Collibra extension origins + version (§4.5).
5. `deploy/.env.prod.example`, `deploy/gcp/create-vm.sh` message, README, docs (`deploy_gcp_vm.md`,
   `collibra_extension_publish.md`, MVP plan) → easyquery examples.
6. Tests: unchanged behaviour (`ODN_PUBLIC_URL`-driven); add a Caddyfile check if we have one (`caddy validate`).

## 6. Decision needed: domain only, or rename the product too?

The repo's product name is **QueryMuse** (`themes/index.ts product: "QueryMuse.AI"`, email sender name, page
titles, privacy policy, Google consent screen app name, Collibra extension name "QueryMuse Collibra Bridge",
Insight Wall name). If easyquery.ai means a **rebrand to EasyQuery**, add (≈ 0.5–1 day): theme/product strings,
logos/favicon, about slides, website copy, privacy policy entity name, email templates, extension name, Google
consent-screen name (re-verification, §4.1), and the docs. Internal names (`odn` package, `ODN_*`) stay.

## 7. Cut-over runbook

**Before (days ahead):**
1. DNS: add `app` and `insights` A records for easyquery.ai; Search Console TXT (§4.1).
2. Google: authorized domain + branding (start re-verification), add redirect URI + JS origin (§4.1).
3. Identity Platform authorized domain (§4.2).
4. Ship the code changes (§5) — harmless while `ODN_HOSTNAME` is still querymuse; publish the extension update and
   ask Collibra users to update (§4.5); tell Collibra admin about token-mode issuer if used.
5. Optional: Resend domain + Microsoft 365 alias (§4.4).

**Switch (≈ 15 min, low-traffic time):**
6. Back up `/opt/odn/shared/.env`; apply the §3 values with `deploy/deploy.sh env <file>`; `deploy/deploy.sh up`.
7. Watch Caddy obtain certificates for app/www/insights.easyquery.ai (`deploy/deploy.sh logs caddy`).

**Verify:**
8. `https://easyquery.ai` shows the marketing page signed out and the app signed in; `/signin` works; `www.` and `app.easyquery.ai` redirect to it;
   sign in with Google (Cotality account), password (Indulge), personal account.
9. Old links: `https://app.querymuse.ai/s/<id>` → 301 → `https://easyquery.ai/s/<id>` renders; preview image
   loads; `https://querymuse.ai`, `www.querymuse.ai`, `insights.querymuse.ai` redirect.
10. Send an invite and a password reset: link opens on easyquery and works.
11. Create a share: `og:image`/`og:url` on easyquery; Teams/Slack card renders.
12. Collibra browser mode works with the new extension; Collibra token mode (if configured) still accepted.
13. CI/deploy notes updated; announce the new address to clients (Cotality, Indulge).

**Rollback:** restore the `.env` backup and `deploy/deploy.sh up` — querymuse config is untouched elsewhere (keep the
old Google redirect URI and Identity Platform domain until the move is confirmed).

## 8. Effort and risks

| Item | Effort | Lead time / risk |
|---|---|---|
| DNS records | minutes | propagation ≤ 1 h |
| Code changes (§5) | ≈ 0.5 day | low |
| Google OAuth + consent screen | 30 min | **brand re-verification: days** — main schedule risk |
| Identity Platform | minutes | low |
| Collibra extension update | 1 h + rollout | users must reload/install; Web Store review if published |
| Email on easyquery (optional) | 1–2 h | DNS verification; can be done after the switch |
| Rebrand (if decided, §6) | 0.5–1 day | consent-screen name change also needs verification |
| Cut-over + checks | ≈ 1 h | everyone signs in once again; local prefs reset |

## 9. Client-specific entry points (decided 2026-11-23)

**Decision:** start with **a path per client** (`easyquery.ai/cotality`, `easyquery.ai/indulge`); add **wildcard
subdomains** (`cotality.easyquery.ai`) when a client asks; offer **client-owned domains** (`data.cotality.com`) later
as an enterprise feature on the same mechanism.

### 9.1 Phase 1 — path per client (≈ 0.5 day, no DNS, no certificates) — ✅ built 2026-11-23

Implemented: `GET /v1/auth/entry/{slug}` (via the `odn_tenant_entry()` SECURITY DEFINER function, migration `0025`,
which also adds the reserved-slug CHECK; `provision tenant` refuses reserved slugs), page `frontend/app/[slug]`
(branded sign-in: *Continue with Google* with the `hd` hint, *Continue* for OIDC, email + password for password
organizations; personal tenant → `/?account=personal`; signed in elsewhere → *Go to your workspace* / *Sign out and
sign in to …*), the slug remembered in the organization cookie, sign-out returning to the link, and the link in
Organization → profile ("Sign-in link" + Copy). Invite emails don't include the link yet.

- **Route:** `easyquery.ai/<slug>` for an active organization slug (`cotality`, `indulge`, …). Unknown slugs → 404
  (no organization enumeration beyond what discovery already reveals). Reserve slugs that clash with app/marketing
  paths (`signin`, `auth`, `api`, `s`, `insights`, `privacy`, `terms`, `pricing`, `about`, `blog`, `site`,
  `downloads`, `.well-known`, `_next` …) — validate in `provision tenant` and with a DB check.
- **Signed out:** the client's branded sign-in page — organization pre-filled and locked, their name/logo/theme
  (`tenantTheme`), the right method straight away (Google button for Cotality, email + password for Indulge, later
  *Continue with Microsoft*). Email-domain checks unchanged.
- **Signed in:** member of that organization → redirect to the app at `easyquery.ai/`; member of another
  organization → "You're signed in to *Indulge*" with *Go to your workspace* / *Sign out*.
- **Remembered:** the slug is stored in the existing organization cookie, so the next visit to `easyquery.ai/signin`
  is pre-filled too.
- **Admin:** Organization tab shows the entry link ("Your sign-in page: easyquery.ai/indulge") to put in onboarding
  emails, intranet links and bookmarks; invite emails link to it.
- Works unchanged on querymuse.ai before the move (`app.querymuse.ai/indulge`).

### 9.2 Phase 2 — wildcard subdomains (≈ 2–3 days, when a client asks)

| Piece | How |
|---|---|
| DNS | **One** wildcard record `*.easyquery.ai` → `136.71.108.203` (specific records like `insights.` still win). Nothing per client |
| TLS | **Caddy on-demand TLS**: certificate issued on the first request to `<slug>.easyquery.ai`, after Caddy's `ask` hook calls an internal API endpoint that answers 200 only for active slugs (prevents abuse). No DNS-provider API needed (a wildcard certificate would need DNS-01 → moving DNS off GoDaddy to e.g. Cloudflare). Let's Encrypt limit: 50 new certificates per domain per week — ample |
| Sign-in | Google (and Microsoft) don't allow wildcard redirect URIs, so sign-in stays on `easyquery.ai/auth/callback`; the subdomain starts the flow with its host in the signed `state`, the callback creates the session and hands it to `<slug>.easyquery.ai` via a single-use, short-lived code (`/auth/handoff?code=…`) that sets a host-only cookie there |
| Same-origin check | Today one origin (`ODN_PUBLIC_URL`); becomes: `easyquery.ai` or `<slug>.easyquery.ai` **where slug = the session's organization** |
| Tenant binding | Host decides the organization; a session for another organization is refused (or redirected to its own subdomain) |
| Cookies | Host-only per subdomain (no `Domain=.easyquery.ai`) — separate sessions per client, no cross-tenant leakage |
| Links | Share links, emails and previews use the organization's host (`ODN_PUBLIC_URL` becomes per-tenant) |
| Collibra extension | Matches `https://*.easyquery.ai/*` |

### 9.3 Phase 3 — client-owned domains (enterprise, later)

`data.cotality.com`: the client adds one CNAME to `easyquery.ai` (their DNS, their change); we add the hostname to the
organization after a TXT ownership check; Caddy's on-demand TLS issues the certificate (the `ask` hook also accepts
verified custom hostnames); sign-in handoff and tenant binding as in phase 2.

### 9.4 Not chosen

| Option | Why not (now) |
|---|---|
| A DNS record per client | Manual work per client; the wildcard does the same once |
| Wildcard certificate via DNS-01 | Needs a DNS-provider API (GoDaddy restricts it) — on-demand TLS avoids it |
| Shared cookie across subdomains (`Domain=.easyquery.ai`) | One session visible to every client host — weakens tenant isolation |

