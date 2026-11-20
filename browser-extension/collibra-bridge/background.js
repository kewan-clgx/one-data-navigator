// Service worker: performs allowlisted, read-only Collibra GETs with the user's own Collibra browser session.
// Accepts messages only from this extension's content script running on a QueryMuse page. Cookies are sent by
// the browser and never read, returned or stored by the extension.

import { APP_ORIGINS, DEFAULT_BASE_URL, checkRequest, validBaseUrl } from "./policy.js";

const TIMEOUT_MS = 20000;
const MAX_BYTES = 2 * 1024 * 1024;

async function baseUrl() {
  const { baseUrl: stored } = await chrome.storage.sync.get("baseUrl");
  return validBaseUrl(stored || DEFAULT_BASE_URL) || DEFAULT_BASE_URL;
}

function notSignedIn(base) {
  return { ok: false, error: "not_signed_in", login_url: base, message: `Sign in to Collibra at ${base} in this browser, then retry.` };
}

async function collibraGet(path, params) {
  const base = await baseUrl();
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
  if (resp.type === "opaqueredirect" || resp.status === 401) return notSignedIn(base);
  if (resp.status === 403) return { ok: false, error: "forbidden", message: "Your Collibra account does not have permission to read this." };
  if (resp.status === 404) return { ok: false, error: "not_found", message: "Not found in Collibra." };
  if (resp.status === 429) return { ok: false, error: "rate_limited", message: "Collibra is rate-limiting requests; try again shortly." };
  if (!resp.ok) return { ok: false, error: "collibra_error", message: `Collibra returned an error (${resp.status}).` };
  const text = await resp.text();
  if (text.length > MAX_BYTES) return { ok: false, error: "too_large", message: "Collibra response too large." };
  try {
    return { ok: true, base_url: base, data: JSON.parse(text) };
  } catch {
    // Some SSO setups answer with an HTML login page and status 200.
    return notSignedIn(base);
  }
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  const origin = sender.origin || (sender.url ? new URL(sender.url).origin : "");
  if (sender.id !== chrome.runtime.id || !sender.tab || !APP_ORIGINS.includes(origin)) return false;
  if (message?.type === "info") {
    baseUrl().then((base) => sendResponse({ ok: true, base_url: base, version: chrome.runtime.getManifest().version }));
    return true;
  }
  if (message?.type === "get") {
    collibraGet(message.path, message.params).then(sendResponse);
    return true;
  }
  sendResponse({ ok: false, error: "refused", message: "unknown request" });
  return false;
});
