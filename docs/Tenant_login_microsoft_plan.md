# Sign in with Microsoft (Entra ID) for client organizations — plan

**Status:** proposal for reference, not scheduled (2026-11-22). Related: [Tenant_Isolation_User_system.md](./Tenant_Isolation_User_system.md)
(decision D6: "Move SSO tenants onto Identity Platform — later, only if a client brings SAML/Entra").

**Goal:** people at Microsoft 365 companies (e.g. `kevinwan@indulgetheapp.com`, `bchen@brainchip.com`) sign in to
QueryMuse with their existing work email, password and Authenticator MFA — no QueryMuse password to create.

---

## 1. Key point: we never handle their password

QueryMuse can't (and shouldn't) check a Microsoft password itself. The standard way is **single sign-on (OpenID
Connect) with Microsoft Entra ID**: *Continue with Microsoft* → Microsoft's own sign-in page (their password, MFA,
conditional access) → Microsoft returns a signed ID token → QueryMuse signs the user in. Benefits: no new password,
the company's MFA and policies apply, and disabling someone in Microsoft 365 also locks them out of QueryMuse.

## 2. Where we are today

| Sign-in method | Used by | How |
|---|---|---|
| Google (OIDC) | Cotality (`hd=cotality.com`), personal Google accounts | Google redirect; tenant from the `hd` claim |
| Email + password | Indulge (today), personal accounts | Google Identity Platform, one IdP tenant per organization |
| Microsoft / Entra | — | **not built** |

Tenant resolution is generic: a verified token's `(issuer, tenant claim)` maps to an organization through
`tenant_identity_providers` / `odn_resolve_identity` (`backend/src/odn/tenancy/context.py`,
`backend/src/odn/tenancy/oidc.py` `TrustedIssuers`). Microsoft fits this model with the `tid` claim.

## 3. Client checks (2026-11-22)

Looked up from public Microsoft endpoints (`getuserrealm.srf`, `/.well-known/openid-configuration`):

| Organization | Microsoft tenant ID | Type | Notes |
|---|---|---|---|
| Indulge (`indulgetheapp.com`) | `1c48788a-3be4-4237-9224-d74cb9142d41` | **Federated** (brand "propertyfund2.com") | Typical of Microsoft 365 bought through GoDaddy: the password is checked on GoDaddy's page. Works with Entra sign-in, but the customer may not have full admin rights (see §6) |
| Brainchip (`brainchip.com`) | `b488bfc8-6943-4a27-b62c-25efb88dc001` | **Managed** ("Brainchip Inc.") | Plain Microsoft 365 (mail via Exchange Online); straightforward |
| Cotality (`cotality.com`) | (CoreLogic Solutions, LLC — federated) | — | Not needed: Cotality signs in with **Google** (§7) |
| QueryMuse (`querymuse.ai`, our own) | `90d0261c-7c9f-4d3c-a4cb-986b0ed3c5e5` | **Federated to GoDaddy** (`sso.godaddy.com`) | Matters for registering our app and publisher verification (§8) |

Re-check any new client: `curl "https://login.microsoftonline.com/getuserrealm.srf?login=someone@domain&json=1"`
(`NameSpaceType`: Managed / Federated / Unknown = not Microsoft 365) and
`https://login.microsoftonline.com/<domain>/v2.0/.well-known/openid-configuration` (issuer contains the tenant ID).

## 4. Options

| | A. Microsoft provider in Identity Platform | **B. Direct Entra OIDC (chosen)** |
|---|---|---|
| How | Enable the Microsoft provider on the client's Identity Platform tenant; tokens stay Identity Platform tokens (`firebase.tenant`) | QueryMuse registers one **multitenant** app in Entra; the BFF runs an OIDC code + PKCE flow against `login.microsoftonline.com/organizations`; the API trusts Microsoft tokens and maps `tid` → organization |
| Our work | ≈ 1–1.5 days; no API change | ≈ 2 days; new verifier + mapping |
| Existing users | Same user if Identity Platform links accounts by email | Link to the existing user by verified email on first Microsoft sign-in |
| Cost | Identity Platform per-MAU pricing for SSO providers may apply (check) | No per-user fee |
| Lock-in | Tied to Identity Platform | Standard OIDC; works for any Microsoft 365 client |

**Chosen: B**, keeping email + password as a fallback for Indulge.

## 5. Design (option B)

```mermaid
sequenceDiagram
    autonumber
    actor U as User (bchen@brainchip.com)
    participant UI as QueryMuse (Next.js BFF)
    participant MS as Microsoft Entra ID
    participant API as API
    U->>UI: Organization "Brainchip" + email → Continue
    UI->>API: discover → method "microsoft"
    UI->>MS: /organizations/oauth2/v2.0/authorize (PKCE, login_hint, domain_hint)
    MS-->>U: Microsoft sign-in + Authenticator (company policies)
    MS->>UI: /auth/callback/microsoft?code=…
    UI->>MS: token exchange → ID token (iss = …/{tid}/v2.0, tid, oid, email/preferred_username)
    UI->>API: requests with the ID token
    API->>API: verify signature (Microsoft JWKS), audience = our client ID, issuer matches tid
    API->>API: (issuer, tid) → organization via tenant_identity_providers; user by oid, else link by email
```

- **App registration** (our Entra tenant): multitenant ("Accounts in any organizational directory"), web redirect
  `https://app.querymuse.ai/auth/callback/microsoft` (+ localhost for dev), client secret (or certificate) in Secret
  Manager, permissions only `openid profile email` (+ `offline_access` for refresh). Branding: name, logo, home,
  privacy (`https://www.querymuse.ai/privacy`), terms (to add), publisher domain `querymuse.ai`.
- **Backend:** `tenants.auth_method` gains `'microsoft'`; a Microsoft `TokenVerifier` per mapped tenant
  (issuer `https://login.microsoftonline.com/{tid}/v2.0`, JWKS `…/discovery/v2.0/keys`, audience = client ID,
  tenant claim `tid`). Only `tid`s mapped in `tenant_identity_providers` are accepted — an unmapped Microsoft
  tenant gets "your organization isn't set up". `provision idp <slug> https://login.microsoftonline.com/<tid>/v2.0 <tid>`.
- **Identity:** user key = (`issuer`, `oid`). Email from `email`, else `preferred_username`/UPN (must be on one of the
  organization's verified domains). First Microsoft sign-in of an existing password user links by verified email,
  so their conversations and results stay.
- **Frontend:** discovery returns `microsoft` (or both `microsoft` + `password` for a fallback) → *Continue with
  Microsoft* button; new `/auth/login/microsoft` and `/auth/callback/microsoft` routes; refresh via Microsoft refresh
  tokens like Google/Identity Platform today.
- **Admin:** Organization tab shows "Sign-in: Microsoft (Entra) + email/password fallback".
- **Tests:** token from an unmapped `tid` rejected; wrong audience rejected; email outside verified domains rejected;
  email-linking of an existing user; password fallback still works.

## 6. What the client's Microsoft 365 admin must do

1. **Approve QueryMuse once (admin consent)** — expected in practice. A Global Administrator, Cloud Application
   Administrator or Application Administrator opens
   `https://login.microsoftonline.com/<their tenant ID>/adminconsent?client_id=<our client ID>` and clicks **Accept**.
   It grants only sign-in + email + basic profile (no mail, files or directory).
2. *(Optional)* **Restrict who can sign in:** Entra admin center → Enterprise applications → QueryMuse →
   *Assignment required = Yes*, then assign users/groups.
3. Nothing else: no secrets, no app registration on their side; their MFA and conditional access apply automatically.

**GoDaddy-managed tenants (Indulge):** GoDaddy often doesn't give the customer a full Global Administrator, so the
consent click may need GoDaddy support or *defederating* the tenant from GoDaddy first. This is the most likely
snag; Brainchip (managed) shouldn't have it.

## 7. Why Cotality needed no admin approval

Cotality signs in to QueryMuse with **Google** (their Google Workspace / Cloud Identity, `hd=cotality.com`), not
Microsoft. Google Workspace's default lets users authorize third-party apps that ask only for basic scopes
(openid / email / profile) unless the admin restricts it under **Security → API controls**; our Google OAuth brand
("QueryMuse.ai") is verified. Microsoft is stricter by default: most tenants allow user consent only for apps from
**verified publishers** (and many disable user consent entirely), so an unverified QueryMuse app needs admin
consent.

Caveat: Cotality's Google admin can block QueryMuse at any time (users would see "Access blocked: this app hasn't
been approved by your admin"). Recommended: ask them to mark QueryMuse as **Trusted** under API controls → App
access control. Their admin can also see who has signed in to QueryMuse.

## 8. Microsoft publisher verification (blue "verified" badge)

Free; mainly lets users in companies with Microsoft's recommended consent setting approve a basic-sign-in app
themselves, and makes admins more comfortable. It **doesn't** override a company policy that disables user consent.
Sources: [overview](https://learn.microsoft.com/en-us/entra/identity-platform/publisher-verification-overview),
[how-to](https://learn.microsoft.com/en-us/entra/identity-platform/mark-app-as-publisher-verified).

**Steps**

0. **Full admin of our tenant.** `querymuse.ai` is federated to GoDaddy. Microsoft requires the app's tenant to have
   `querymuse.ai` as a verified domain (publisher domain can't be `*.onmicrosoft.com`), and a domain can be verified
   in only one tenant — so ask GoDaddy to **defederate / move to direct** the querymuse.ai Microsoft 365 (mailboxes
   keep working; manage at admin.microsoft.com). Confirm the current process with GoDaddy.
1. **Join the Microsoft AI Cloud Partner Program** (formerly MPN), free, at partner.microsoft.com → Join, signed in
   with a work account in our tenant (e.g. `kewan@querymuse.ai`). Enter the company's legal name, address and an
   `@querymuse.ai` contact. Microsoft verifies email, employment and the legal business (may ask for registration
   documents; typically days to ~2 weeks). Use the resulting **Partner One ID of the partner global account (PGA)**
   — not a location ID.
2. **Register the app** (§5) with publisher domain `querymuse.ai`.
3. **Verify:** sign in with MFA as a user who is Application Administrator / Cloud Application Administrator in
   Entra **and** CPP Admin / Accounts Admin in Partner Center → App registrations → QueryMuse → **Branding &
   properties** → **Add Partner ID to verify publisher** → **Verify and save**. The badge appears on consent prompts
   shortly after.

## 9. Rollout plan (when we do it)

| Step | Owner | Effort / lead time |
|---|---|---|
| 1. Defederate querymuse.ai Microsoft 365 from GoDaddy | us + GoDaddy support | days |
| 2. Enroll in the Cloud Partner Program (start early — slowest step) | us | days–2 weeks (Microsoft) |
| 3. Register the multitenant app; add a Terms page | us | 0.5 day |
| 4. Build option B (§5) + tests; deploy | us | ≈ 2 days |
| 5. Map tenants: `provision idp indulge …/1c48788a-…/v2.0 1c48788a-…`, same for Brainchip (`b488bfc8-…`) | us | minutes |
| 6. Send each client's admin the consent link (§6) | client admin | minutes (Indulge: may need GoDaddy) |
| 7. Publisher verification (§8) once the Partner ID arrives | us | minutes |

Steps 4–6 don't wait for 7: clients can use *Continue with Microsoft* as soon as their admin approves.

## 10. Open questions

- Keep the password fallback for Microsoft organizations permanently, or remove it once everyone has signed in
  with Microsoft?
- Allow anyone in a mapped Microsoft tenant to join automatically (join policy `domain`), or invited people only?
- Do we also want Microsoft sign-in for **personal** Microsoft accounts (outlook.com)? Not planned — work/school
  accounts only (`/organizations` endpoint).
