// Request policy shared by the service worker and the Node tests: which Collibra reads QueryMuse may ask for.
// Only bounded, read-only REST GETs on a fixed allowlist; everything else is refused before any request is made.

export const DEFAULT_BASE_URL = "https://corelogic.collibra.com";
export const APP_ORIGINS = ["https://app.querymuse.ai", "http://localhost:3100"];

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const ROUTES = [
  { path: /^users\/current$/, params: {} },
  {
    path: /^assets$/,
    params: { name: "text", nameMatchMode: ["ANYWHERE", "START", "EXACT"], domainId: "uuid", limit: "limit", offset: "offset" },
    required: ["name", "limit"],
  },
  { path: /^assets\/[0-9a-f-]{36}$/i, params: {} },
  { path: /^attributes$/, params: { assetId: "uuid", limit: "limit", offset: "offset" }, required: ["assetId", "limit"] },
  {
    path: /^relations$/,
    params: { sourceId: "uuid", targetId: "uuid", limit: "limit", offset: "offset" },
    required: ["limit"],
    oneOf: ["sourceId", "targetId"],
  },
];

/** A Collibra origin the bridge may call: https on *.collibra.com, or http://localhost for development. */
export function validBaseUrl(value) {
  let url;
  try {
    url = new URL(value);
  } catch {
    return null;
  }
  if (url.pathname !== "/" || url.search || url.hash || url.username || url.password) return null;
  const host = url.hostname.toLowerCase();
  if (url.protocol === "https:" && (host === "collibra.com" || host.endsWith(".collibra.com"))) return url.origin;
  if (url.protocol === "http:" && (host === "localhost" || host === "127.0.0.1")) return url.origin;
  return null;
}

function validValue(kind, value) {
  const s = String(value);
  if (Array.isArray(kind)) return kind.includes(s);
  if (kind === "uuid") return UUID.test(s);
  if (kind === "limit") return /^\d+$/.test(s) && +s >= 1 && +s <= 100;
  if (kind === "offset") return /^\d+$/.test(s) && +s <= 100000;
  if (kind === "text") return s.trim().length >= 1 && s.length <= 200 && !/[\x00-\x1f]/.test(s);
  return false;
}

/** Validate {path, params}; returns the relative URL ("/rest/2.0/...") or throws with a reason. */
export function checkRequest(path, params = {}) {
  if (typeof path !== "string" || path.includes("..") || path.startsWith("/")) throw new Error("path not allowed");
  const route = ROUTES.find((r) => r.path.test(path));
  if (!route) throw new Error("path not allowed");
  if (typeof params !== "object" || params === null || Array.isArray(params)) throw new Error("invalid params");
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (!(key in route.params)) throw new Error(`parameter ${key} not allowed`);
    if (!validValue(route.params[key], value)) throw new Error(`invalid ${key}`);
    query.set(key, String(value));
  }
  for (const key of route.required ?? []) if (!query.has(key)) throw new Error(`${key} is required`);
  if (route.oneOf && route.oneOf.filter((k) => query.has(k)).length !== 1) throw new Error(`give one of ${route.oneOf}`);
  const qs = query.toString();
  return `/rest/2.0/${path}${qs ? `?${qs}` : ""}`;
}
