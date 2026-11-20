# QueryMuse Collibra Bridge (browser extension, prototype)

Lets QueryMuse's agent read the Collibra catalog **with your own Collibra sign-in in this browser**. QueryMuse
needs no Collibra configuration or service account, nothing is stored by QueryMuse, and Collibra applies your own
permissions. It is the stopgap until Collibra trusts QueryMuse's token issuer (option A,
[docs/collibra_int_phase5_plan.md](../../docs/collibra_int_phase5_plan.md)).

## Install (Chrome or Edge, developer mode)

1. Open `chrome://extensions` (or `edge://extensions`) and turn on **Developer mode**.
2. **Load unpacked** → choose this folder (`browser-extension/collibra-bridge`).
3. Click the extension's icon: the Collibra address defaults to `https://corelogic.collibra.com`; change and
   **Save** if yours differs.
4. Sign in to Collibra in the **same browser profile** as usual (SSO/MFA).
5. Open (or reload) `https://app.querymuse.ai` and ask, e.g. *"Using Collibra, what is the definition of
   Corporate Revenue?"* The chat shows `collibra_browser_*` steps.

If Collibra says you are not signed in, open the Collibra address in a tab, sign in, and ask again.

## How it works

```
QueryMuse agent (server) ─ tool call ─▶ QueryMuse page (your browser) ─postMessage─▶ content script
   ▲                                                                                  │ chrome.runtime
   └──────────── tool result (catalog metadata) ◀── page ◀── service worker ─GET─▶ Collibra (your session)
```

- `content.js` runs only on `https://app.querymuse.ai` (and `http://localhost:3100` for development) and relays
  messages from that page to the service worker.
- `background.js` performs the request with the browser's Collibra session (`credentials: "include"`), refusing
  anything outside `policy.js`: only `GET /rest/2.0/` `users/current`, `assets` (name search), `assets/{id}`,
  `attributes`, `relations`, with bounded `limit` (1–100) and validated parameters. No writes, workflows or
  GraphQL. An SSO redirect or HTML page is reported as "not signed in".
- Cookies are sent by the browser; the extension never reads, returns or stores them. Only the JSON catalog
  metadata goes back to the page, and from there to the agent (and the AI model), like the KWAgent connector.

## Test

```sh
npm test        # request-policy tests (node --test)
```

End to end (2026-11-19): local QueryMuse + this extension in Playwright Chromium + a fake Collibra that requires
a browser session cookie and redirects to a login page otherwise. Verified: not-signed-in detection, refused
paths, session cookie sent after sign-in, and a chat answer built only from Collibra data via the
`collibra_browser_*` tools. Not yet verified against the real Collibra tenant.

## Distribution (later)

Developer-mode install is for the pilot. For wider use: Chrome Web Store (unlisted) or the company's managed
extension policy (Cotality IT), with the extension ID pinned.
