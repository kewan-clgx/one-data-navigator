# EasyQuery Collibra Bridge (browser extension, prototype)

Lets EasyQuery's agent read the Collibra catalog **with your own Collibra sign-in in this browser**. EasyQuery
needs no Collibra configuration or service account, nothing is stored by EasyQuery, and Collibra applies your own
permissions. It is the stopgap until Collibra trusts EasyQuery's token issuer (option A,
[docs/collibra_int_phase5_plan.md](../../docs/collibra_int_phase5_plan.md)).

## Install (Chrome or Edge, developer mode)

Your EasyQuery admin first adds the organization's Collibra in **Settings → Collibra data catalog** (browser mode).
Then each user:

1. Downloads the zip from **Settings → Collibra** (or the Collibra tab, or the link the assistant gives):
   `https://easyquery.ai/downloads/easyquery-collibra-bridge.zip`, and unzips it.
2. Opens `chrome://extensions` (or `edge://extensions`), turns on **Developer mode**, clicks **Load unpacked** and
   chooses the unzipped `querymuse-collibra-bridge` folder.
3. Signs in to Collibra in the **same browser profile** as usual (SSO/MFA).
4. Reloads EasyQuery and uses the **Collibra** tab or asks, e.g. *"Using Collibra, what is the definition of
   Corporate Revenue?"*

The extension uses the Collibra address your admin configured; its own setting (icon → address) is only a
fallback. Developers can also load this folder (`frontend/extension/collibra-bridge`) directly.

If Collibra says you are not signed in, open the Collibra address in a tab, sign in, and ask again.

## How it works

```
EasyQuery agent (server) ─ tool call ─▶ EasyQuery page (your browser) ─postMessage─▶ content script
   ▲                                                                                  │ chrome.runtime
   └──────────── tool result (catalog metadata) ◀── page ◀── service worker ─GET─▶ Collibra (your session)
```

- `content.js` runs only on `https://easyquery.ai` and `https://app.querymuse.ai` (and `http://localhost:3100` for development) and relays
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

End to end (2026-11-19): local EasyQuery + this extension in Playwright Chromium + a fake Collibra that requires
a browser session cookie and redirects to a login page otherwise. Verified: not-signed-in detection, refused
paths, session cookie sent after sign-in, and a chat answer built only from Collibra data via the
`collibra_browser_*` tools. Verified on the real `corelogic.collibra.com` tenant the same day after SSO sign-in.

If it says "not signed in" although you are, click the extension icon → **Test connection**: it shows what
Collibra answered (redirect to sign-in, 401, or a web page) and where a redirect leads.

## Distribution

Developer-mode install is for the pilot. Publishing (Chrome Web Store, unlisted) and client IT force-install are
described in [docs/collibra_extension_publish.md](../../../docs/collibra_extension_publish.md).
