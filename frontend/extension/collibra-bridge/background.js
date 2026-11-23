// Service worker: performs allowlisted, read-only Collibra GETs with the user's own Collibra browser session.
// Accepts messages only from this extension's content script running on a EasyQuery page. Cookies are sent by
// the browser and never read, returned or stored by the extension.

import { APP_ORIGINS, DEFAULT_BASE_URL, checkRequest, validBaseUrl } from "./policy.js";

const TIMEOUT_MS = 20000;
const MAX_BYTES = 2 * 1024 * 1024;

async function baseUrl() {
  const { baseUrl: stored } = await chrome.storage.sync.get("baseUrl");
  return validBaseUrl(stored || DEFAULT_BASE_URL) || DEFAULT_BASE_URL;
}

function notSignedIn(base, reason, diagnostics = {}) {
  return {
    ok: false,
    error: "not_signed_in",
    login_url: base,
    reason,
    diagnostics,
    message: `Collibra did not accept this browser's session (${reason}). Sign in at ${base} in this browser profile, then retry.`,
  };
}

/** Where an unauthenticated request ends up (follows redirects); explains "redirected to sign-in" vs other cases. */
async function redirectTarget(url) {
  try {
    const r = await fetch(url, { credentials: "include", redirect: "follow", headers: { Accept: "application/json" } });
    return { followed_to: new URL(r.url).origin + new URL(r.url).pathname, followed_status: r.status };
  } catch {
    return { followed_to: "an external sign-in page (not reachable by the extension)" };
  }
}

async function collibraGet(path, params, requestedBase) {
  // The organization's Collibra address comes from EasyQuery (admin setting); it must still be a Collibra origin.
  const base = requestedBase ? validBaseUrl(requestedBase) : await baseUrl();
  if (!base) return { ok: false, error: "refused", message: "Not a Collibra address (https://<name>.collibra.com)." };
  let relative;
  try {
    relative = checkRequest(path, params);
  } catch (e) {
    return { ok: false, error: "refused", message: e.message };
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  let resp;
  try {
    resp = await fetch(base + relative, {
      method: "GET",
      credentials: "include",
      redirect: "manual", // an SSO redirect means there is no Collibra session in this browser
      headers: { Accept: "application/json" },
      signal: controller.signal,
    });
  } catch (e) {
    return { ok: false, error: controller.signal.aborted ? "timeout" : "unreachable", message: `Collibra at ${base} is not reachable.` };
  } finally {
    clearTimeout(timer);
  }
  if (resp.type === "opaqueredirect") {
    return notSignedIn(base, "Collibra redirected to sign-in", await redirectTarget(base + relative));
  }
  if (resp.status === 401) {
    return notSignedIn(base, "Collibra answered 401", { www_authenticate: resp.headers.get("WWW-Authenticate") || null });
  }
  if (resp.status === 403) return { ok: false, error: "forbidden", message: "Your Collibra account does not have permission to read this." };
  if (resp.status === 404) return { ok: false, error: "not_found", message: "Not found in Collibra." };
  if (resp.status === 429) return { ok: false, error: "rate_limited", message: "Collibra is rate-limiting requests; try again shortly." };
  if (!resp.ok) return { ok: false, error: "collibra_error", message: `Collibra returned an error (${resp.status}).` };
  const contentType = resp.headers.get("Content-Type") || "";
  const text = await resp.text();
  if (text.length > MAX_BYTES) return { ok: false, error: "too_large", message: "Collibra response too large." };
  try {
    return { ok: true, base_url: base, data: JSON.parse(text) };
  } catch {
    // Some SSO setups answer with an HTML login page and status 200.
    return notSignedIn(base, "Collibra returned a web page instead of data", { status: resp.status, content_type: contentType });
  }
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  const origin = sender.origin || (sender.url ? new URL(sender.url).origin : "");
  const fromApp = Boolean(sender.tab) && APP_ORIGINS.includes(origin);
  const fromOwnPage = !sender.tab && sender.url?.startsWith(`chrome-extension://${chrome.runtime.id}/`);
  if (sender.id !== chrome.runtime.id || !(fromApp || fromOwnPage)) return false;
  if (message?.type === "info") {
    baseUrl().then((base) => sendResponse({ ok: true, base_url: base, version: chrome.runtime.getManifest().version }));
    return true;
  }
  if (message?.type === "get") {
    collibraGet(message.path, message.params, message.base_url).then(sendResponse);
    return true;
  }
  sendResponse({ ok: false, error: "refused", message: "unknown request" });
  return false;
});
