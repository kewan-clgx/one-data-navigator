# Publishing the QueryMuse Collibra Bridge (without developer mode)

**Status:** Not started (2026-11-19). Today users install the extension from a zip in developer mode
(Settings → Collibra → *Download extension*). This guide replaces that with a normal install.
**Extension source:** [`frontend/extension/collibra-bridge`](../frontend/extension/collibra-bridge/README.md) ·
**Parent tracker:** [collibra_int_phase5_plan.md](collibra_int_phase5_plan.md) §4.3–4.4

Two routes that work together:

1. **Chrome Web Store, unlisted** (QueryMuse does this once): users click **Add to Chrome**; updates are
   automatic; works in Chrome and Edge.
2. **Managed policy** (each client's IT, e.g. Cotality): IT force-installs that same store listing on their staff's
   browsers, so users install nothing.

A self-hosted `.crx` (no Web Store) is a fallback, described at the end.

---

## Route 1: Chrome Web Store, unlisted

"Unlisted" means the item is not shown in store search; only people with the link can install it. One listing serves
every client organization, because the Collibra address comes from each organization's admin setting.

### 1.1 Developer account (one time)

- [ ] Sign in at <https://chrome.google.com/webstore/devconsole> with a **company** Google account, accept the
      developer agreement and pay the one-time registration fee (US$5).
  - The account email **cannot be changed later**. To use `support@querymuse.ai` (a Microsoft 365 mailbox), first
    create a Google account with that existing address (no Gmail needed).
- [ ] Optional: **verified publisher**. Verify `querymuse.ai` in Google Search Console (the same DNS TXT check used for
      the Google sign-in branding) and link it in the dashboard's account settings; the listing then shows a
      verified-publisher badge.

### 1.2 Prepare the store package (QueryMuse codebase)

- [ ] **Icons:** 16, 48 and 128 px PNGs in the extension, referenced from `manifest.json` (`icons`, `action.default_icon`).
- [ ] **Store build:** a script that writes `querymuse-collibra-bridge-<version>-store.zip` from the extension folder,
      **without** the development entries (`http://localhost:3100/*` content-script match and the optional
      `http://localhost/*` / `http://127.0.0.1/*` host permissions). The zip must have `manifest.json` at its root.
- [ ] **Privacy policy:** add a section about the extension to `https://www.querymuse.ai/privacy`: it runs only on
      QueryMuse pages, makes read-only Collibra requests with the user's own Collibra session, never reads or stores
      cookies, and returns only catalog metadata to QueryMuse.
- [ ] **Listing text and a 1280×800 screenshot** (Collibra tab with a search result).

### 1.3 Create the item

In the dashboard → **New item** → upload the store zip, then fill in:

| Section | What to enter |
|---|---|
| Store listing | Name *QueryMuse Collibra Bridge*; short description *"Use your Collibra data catalog in QueryMuse with your own Collibra sign-in. Read-only."*; category *Productivity* (or *Developer Tools*); 128 px icon; screenshot; homepage `https://www.querymuse.ai`; support `support@querymuse.ai` |
| Privacy → single purpose | *"Lets QueryMuse (app.querymuse.ai) read the Collibra data catalog using the user's own Collibra sign-in in this browser."* |
| Privacy → permission justification: `storage` | *"Stores the Collibra address used when QueryMuse does not provide one."* |
| Privacy → permission justification: host `https://*.collibra.com/*` | *"Makes read-only Collibra REST requests (asset search, asset details, attributes, relations, current user) with the user's existing Collibra session, only when the user uses Collibra in QueryMuse."* |
| Privacy → remote code | No remote code. |
| Privacy → data usage | Handles **website content** (Collibra catalog metadata) to provide the single purpose; not sold, not used for unrelated purposes or creditworthiness; privacy policy `https://www.querymuse.ai/privacy`. |
| Distribution | Visibility **Unlisted**; all regions. |

- [ ] **Submit for review.** Usually a few days; extensions with host access to other sites can get an in-depth
      review. Every later version is reviewed again, then reaches users automatically.

### 1.4 After approval

- [ ] Record the **extension ID** and store URL here: `ID: ______` · `URL: https://chromewebstore.google.com/detail/______`
- [ ] Switch the app from zip + developer mode to an **Add to Chrome** button (Settings → Collibra, the Collibra tab,
      and the agent's chat guidance), e.g. via a setting `ODN_COLLIBRA_EXTENSION_URL`; keep the zip download as a
      fallback for developers.
- [ ] Ask existing pilot users to remove the developer-mode copy and install from the store (they are separate
      extensions to Chrome).

### Releasing updates

1. Bump `version` in `manifest.json` (and `EXTENSION_MIN_VERSION` in `frontend/lib/collibra.ts` if the app needs it).
2. Build the store zip, upload it in the dashboard (**Package → Upload new package**), submit.
3. After approval, browsers update within hours; the app warns users still on an older version.

---

## Route 2: Client IT force-install (managed policy)

Send the client's IT team the **extension ID**, the store URL and this summary: *read-only, runs only on
`app.querymuse.ai`, talks only to `*.collibra.com` with the user's own session, no data stored by the extension.*
They add it to their browser policy:

| Where IT manages browsers | Setting |
|---|---|
| Google Admin console (Chrome Enterprise Core) | Devices → Chrome → Apps & extensions → Users & browsers → **+ Add from Chrome Web Store by ID** → installation policy **Force install** (optionally *Force install + pin*) |
| Windows Chrome (Group Policy / Intune ADMX) | `ExtensionInstallForcelist` = `<id>;https://clients2.google.com/service/update2/crx` |
| macOS Chrome (Jamf / Intune configuration profile, domain `com.google.Chrome`) | `ExtensionInstallForcelist` array with the same value |
| Microsoft Edge (GPO / Intune, Edge policies) | Edge `ExtensionInstallForcelist` with the same value (Edge installs Chrome Web Store items by ID) |

- If the organization **blocks extensions by default** (`ExtensionInstallBlocklist = *`), IT also adds the ID to
  `ExtensionInstallAllowlist`.
- If IT restricts **runtime host access** (`runtime_blocked_hosts` / `ExtensionSettings`), `*.collibra.com` and
  `app.querymuse.ai` must remain allowed for this extension.
- Users then only need to be signed in to Collibra in that browser. **Test connection** in Settings → Collibra
  confirms it.

Per client, record here: organization · IT contact · requested on · policy applied on.

| Organization | IT contact | Requested | Applied |
|---|---|---|---|
| Cotality | | | |

---

## Fallback: self-hosted `.crx` (no Web Store)

Only if a client forbids Web Store items. Chrome on Windows and macOS installs off-store extensions **only through
enterprise policy on managed devices**, so this still needs Route 2.

1. Pack the extension into a `.crx` with a private key that we keep (Secret Manager); the key fixes the extension ID.
2. Host the `.crx` and an update manifest (`updates.xml`: app ID, version, codebase URL) on `app.querymuse.ai`.
3. IT force-installs with `ExtensionInstallForcelist` = `<id>;https://app.querymuse.ai/extension/updates.xml`.
4. Each release: re-pack with the same key, bump the version in `updates.xml`.

More upkeep (signing key, update hosting, no store review signal), so prefer the store route.

---

## Log

| Date | Note |
|---|---|
| 2026-11-19 | Guide written; extension v0.2.0 installed via zip + developer mode. |
