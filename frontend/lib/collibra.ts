/**
 * Collibra for the browser (docs/collibra_int_phase5_plan.md): the organization's environments (admin setting),
 * the QueryMuse Collibra Bridge extension, and one client per environment that reads Collibra either through the
 * user's own browser session (access_mode "browser") or QueryMuse's API (access_mode "token"). Results have the
 * same shape as the server-side collibra_* tools.
 */

import { useCallback, useEffect, useState } from "react";

import { api } from "./client-api";

export type CollibraEnv = { env: string; base_url: string; access_mode: "browser" | "token" };
export type Json = Record<string, any>;
export type BridgeInfo = { version: string } | null;

export const COLLIBRA_CHANGED = "odn:collibra-changed";
export const EXTENSION_DOWNLOAD_URL = "/downloads/querymuse-collibra-bridge.zip";
export const EXTENSION_MIN_VERSION = "0.2.0";

/** Plain-language setup steps (Settings, the Collibra tab and the agent's chat guidance use the same text). */
export function extensionSteps(env?: CollibraEnv | null): string[] {
  return [
    "Download the QueryMuse Collibra Bridge (zip) and unzip it.",
    "In Chrome or Edge open chrome://extensions (edge://extensions), turn on Developer mode, click “Load unpacked” and choose the unzipped querymuse-collibra-bridge folder.",
    `Sign in to Collibra${env ? ` at ${env.base_url}` : ""} in the same browser as usual (SSO).`,
    "Reload QueryMuse. Collibra then works in chat and in the Collibra tab with your own Collibra permissions.",
  ];
}

const APP = "querymuse-app";
const BRIDGE = "querymuse-collibra-bridge";
const TIMEOUT_MS = 30000;
const MAX_TEXT = 2000;
// Data-profiling statistics Collibra attaches to columns: noise for definitions/mapping (same list as the server).
const PROFILING =
  /percentile|quartile|decile|^mean$|^median$|variance|standard deviation|minimum value|maximum value|distinct values|row count|empty values|categorical data/i;

// -- environments ---------------------------------------------------------------------------------------------

export function useCollibraEnvironments(): { envs: CollibraEnv[] | null; reload: () => void } {
  const [envs, setEnvs] = useState<CollibraEnv[] | null>(null);
  const reload = useCallback(() => {
    api<{ environments: CollibraEnv[] }>("collibra/environments")
      .then((r) => setEnvs(r.environments))
      .catch(() => setEnvs([]));
  }, []);
  useEffect(() => {
    reload();
    window.addEventListener(COLLIBRA_CHANGED, reload);
    return () => window.removeEventListener(COLLIBRA_CHANGED, reload);
  }, [reload]);
  return { envs, reload };
}

// -- bridge extension ---------------------------------------------------------------------------------------

type BridgeResult =
  | { ok: true; base_url: string; data: any }
  | { ok: false; error: string; message: string; login_url?: string; reason?: string; diagnostics?: Json };

export function bridgeGet(baseUrl: string, path: string, params: Record<string, string | number> = {}): Promise<BridgeResult> {
  return new Promise((resolve) => {
    const id = crypto.randomUUID();
    const timer = window.setTimeout(() => {
      window.removeEventListener("message", onMessage);
      resolve({ ok: false, error: "no_bridge", message: "The QueryMuse Collibra Bridge extension did not answer (is it installed and enabled?)." });
    }, TIMEOUT_MS);
    function onMessage(event: MessageEvent) {
      if (event.source !== window || event.data?.source !== BRIDGE || event.data?.id !== id) return;
      window.clearTimeout(timer);
      window.removeEventListener("message", onMessage);
      resolve(event.data.result as BridgeResult);
    }
    window.addEventListener("message", onMessage);
    window.postMessage({ source: APP, type: "get", id, path, params, base_url: baseUrl }, window.location.origin);
  });
}

/** The installed bridge extension, if any (it announces itself on page load and when pinged). */
export function useCollibraBridge(): BridgeInfo {
  const [info, setInfo] = useState<BridgeInfo>(null);
  useEffect(() => {
    function onMessage(event: MessageEvent) {
      if (event.source !== window || event.data?.source !== BRIDGE || event.data?.type !== "ready") return;
      setInfo({ version: String(event.data.version) });
    }
    window.addEventListener("message", onMessage);
    window.postMessage({ source: APP, type: "ping" }, window.location.origin);
    return () => window.removeEventListener("message", onMessage);
  }, []);
  return info;
}

export function bridgeOutdated(info: BridgeInfo): boolean {
  if (!info) return false;
  const v = (s: string) => s.split(".").map((n) => Number(n) || 0);
  const [a, b] = [v(info.version), v(EXTENSION_MIN_VERSION)];
  for (let i = 0; i < 3; i++) if ((a[i] ?? 0) !== (b[i] ?? 0)) return (a[i] ?? 0) < (b[i] ?? 0);
  return false;
}

// -- short-lived cache (this browser = this user; cleared on reload/sign-out) ---------------------------------

const CACHE_TTL_MS = 10 * 60 * 1000;
const cache = new Map<string, { expires: number; data: any }>();

async function cachedBridgeGet(baseUrl: string, path: string, params: Record<string, string | number> = {}): Promise<BridgeResult> {
  const key = `${baseUrl} ${path} ${JSON.stringify(Object.entries(params).sort())}`;
  const hit = cache.get(key);
  if (hit && hit.expires > Date.now()) return { ok: true, base_url: baseUrl, data: hit.data };
  const result = await bridgeGet(baseUrl, path, params);
  if (result.ok) {
    if (cache.size > 1000) cache.clear();
    cache.set(key, { expires: Date.now() + CACHE_TTL_MS, data: result.data });
  }
  return result;
}

// -- ranking (same rules as odn.platform.collibra.client.rank_score) --------------------------------------

const TYPE_WEIGHT: Record<string, number> = {
  "business term": 40, "product field": 35, "data attribute": 30, "data element": 28, "report attribute": 25,
  "api field": 22, field: 20, column: 12, table: 10, "database view": 10, schema: 4,
};
const STATUS_WEIGHT: Record<string, number> = {
  accepted: 15, approved: 15, reviewed: 8, candidate: 2, obsolete: -60, deprecated: -60,
};
const TECHNICAL = /[>.\/]|\(column\)|\(table\)/;
const words = (t: string) => t.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);

export function rankScore(asset: Json, query: string): number {
  const label = String(asset.display_name || asset.name || "").trim();
  const name = String(asset.name || "").trim();
  const q = words(query).join(" ");
  const lw = words(label).join(" ");
  let score = TYPE_WEIGHT[String(asset.type || "").toLowerCase()] ?? 15;
  score += STATUS_WEIGHT[String(asset.status || "").toLowerCase()] ?? 0;
  if (q && lw === q) score += 40;
  else if (q && lw.startsWith(q)) score += 25;
  else if (q && lw.includes(q)) score += 15;
  const qw = new Set(words(query));
  if (qw.size) score += (15 * words(label).filter((w, i, a) => qw.has(w) && a.indexOf(w) === i).length) / qw.size;
  if (TECHNICAL.test(name) && !asset.display_name) score -= 15;
  score -= Math.min(label.length, 160) / 16;
  return Math.round(score * 100) / 100;
}

export function rankAssets<T extends Json>(assets: T[], query: string): T[] {
  return [...assets].sort((a, b) => rankScore(b, query) - rankScore(a, query));
}

// -- shaping (same output as odn.platform.collibra.client) -------------------------------------------------

function plain(value: unknown): unknown {
  if (typeof value !== "string") return value;
  const doc = new DOMParser().parseFromString(value, "text/html");
  const text = (doc.body.textContent ?? "").replace(/\s+/g, " ").trim();
  return text.length <= MAX_TEXT ? text : `${text.slice(0, MAX_TEXT)}…`;
}

const refName = (ref: any): string | null => (ref && typeof ref.name === "string" ? ref.name : null);

export class CollibraError extends Error {
  constructor(message: string, readonly loginUrl?: string) {
    super(message);
  }
}

const clamp = (n: unknown, lo: number, hi: number, dflt: number) => {
  const v = Number(n);
  return Number.isFinite(v) ? Math.max(lo, Math.min(hi, Math.trunc(v))) : dflt;
};

/** A Collibra environment's read operations (search, describe, relations, fields, status). */
export class CollibraSource {
  constructor(readonly env: CollibraEnv) {}

  private get base() {
    return this.env.base_url;
  }

  private stamp(payload: Json): Json {
    return { environment: this.env.env, ...payload, retrieved_at: new Date().toISOString() };
  }

  private async get(path: string, params: Record<string, string | number> = {}): Promise<any> {
    const r = await cachedBridgeGet(this.base, path, params);
    if (!r.ok) throw new CollibraError(r.message, r.login_url);
    return r.data;
  }

  private summary(a: any): Json {
    return {
      id: a.id, name: a.name, display_name: a.displayName ?? null, type: refName(a.type), domain: refName(a.domain),
      status: refName(a.status), url: typeof a.id === "string" ? `${this.base}/asset/${a.id}` : null,
    };
  }

  private async page(path: string, params: Record<string, string | number>, limit: number) {
    const data = await this.get(path, params);
    if (!Array.isArray(data?.results) || typeof data?.total !== "number") throw new CollibraError("Collibra returned an invalid catalog page.");
    const offset = typeof data.offset === "number" ? data.offset : Number(params.offset ?? 0);
    const results = data.results.slice(0, limit);
    const consumed = offset + results.length;
    const has_more = consumed < data.total && results.length > 0;
    return { results, total: data.total, offset, limit, has_more, next_offset: has_more ? consumed : null };
  }

  private api<T>(path: string, init?: RequestInit): Promise<T> {
    return api<T>(`collibra/${encodeURIComponent(this.env.env)}/${path}`, init);
  }

  async status(): Promise<Json> {
    if (this.env.access_mode === "token") return this.api<Json>("status");
    const r = await bridgeGet(this.base, "users/current");
    if (!r.ok) {
      return { environment: this.env.env, signed_in: false, error: r.error, detail: r.message, reason: r.reason ?? r.error,
               diagnostics: r.diagnostics ?? null, login_url: r.login_url ?? this.base };
    }
    return this.stamp({ signed_in: true, username: r.data?.userName ?? null });
  }

  async search(text: string, match = "ANYWHERE", limit = 10, offset = 0): Promise<Json> {
    const lim = clamp(limit, 1, 50, 10);
    const mode = ["ANYWHERE", "START", "EXACT"].includes(match) ? match : "ANYWHERE";
    const off = clamp(offset, 0, 100000, 0);
    if (this.env.access_mode === "token") {
      return this.api<Json>(`assets?${new URLSearchParams({ q: text, match: mode, limit: String(lim), offset: String(off) })}`);
    }
    const p = await this.page("assets", { name: text, nameMatchMode: mode, limit: lim, offset: off }, lim);
    return this.stamp({ ...p, results: rankAssets(p.results.map((a: any) => this.summary(a)), text) });
  }

  private async attributes(assetId: string) {
    const p = await this.page("attributes", { assetId, limit: 100, offset: 0 }, 100);
    return { ...p, results: p.results.map((a: any) => ({ type: refName(a.type), value: plain(a.value) })) };
  }

  private async describeOne(assetId: string): Promise<Json> {
    try {
      const asset = await this.get(`assets/${assetId}`);
      const attrs = await this.attributes(assetId);
      const isProfiling = (a: any) => typeof a.type === "string" && PROFILING.test(a.type);
      const kept = attrs.results.filter((a: any) => !isProfiling(a));
      const definition =
        ["definition", "description"]
          .map((t) => kept.find((a: any) => typeof a.type === "string" && a.type.toLowerCase().includes(t) && a.value))
          .find(Boolean)?.value ?? null;
      return {
        ...this.summary(asset), definition, attributes: kept,
        profiling_attributes_omitted: attrs.results.length - kept.length, attributes_truncated: attrs.has_more,
      };
    } catch (e) {
      return { id: assetId, error: (e as Error).message };
    }
  }

  async describe(assetIds: string[]): Promise<Json> {
    const ids = Array.from(new Set(assetIds.map(String))).slice(0, 20);
    if (this.env.access_mode === "token") return this.api<Json>("describe", { method: "POST", body: JSON.stringify({ asset_ids: ids }) });
    const assets: Json[] = [];
    for (let i = 0; i < ids.length; i += 4) assets.push(...(await Promise.all(ids.slice(i, i + 4).map((id) => this.describeOne(id)))));
    return this.stamp({ assets });
  }

  async relations(assetId: string, direction = "outgoing", limit = 20, offset = 0): Promise<Json> {
    const lim = clamp(limit, 1, 100, 20);
    const dir = direction === "incoming" ? "incoming" : "outgoing";
    const off = clamp(offset, 0, 100000, 0);
    if (this.env.access_mode === "token") {
      return this.api<Json>(`assets/${assetId}/relations?${new URLSearchParams({ direction: dir, limit: String(lim), offset: String(off) })}`);
    }
    const key = dir === "incoming" ? "targetId" : "sourceId";
    const p = await this.page("relations", { [key]: assetId, limit: lim, offset: off }, lim);
    const ref = (r: any) => (r ? { id: r.id, name: r.name ?? r.displayName ?? null, url: `${this.base}/asset/${r.id}` } : null);
    const results = p.results.map((r: any) => ({ relation_type_id: r.type?.id ?? null, source: ref(r.source), target: ref(r.target) }));
    return this.stamp({ asset_id: assetId, direction: dir, ...p, results });
  }

  async fields(assetId: string, limit = 20, offset = 0): Promise<Json> {
    const lim = clamp(limit, 1, 20, 20);
    const off = clamp(offset, 0, 100000, 0);
    if (this.env.access_mode === "token") {
      return this.api<Json>(`assets/${assetId}/fields?${new URLSearchParams({ limit: String(lim), offset: String(off) })}`);
    }
    const rel = await this.relations(assetId, "outgoing", lim, off);
    const ids = rel.results.map((r: any) => r.target?.id).filter((x: unknown): x is string => typeof x === "string");
    const described = ids.length ? (await this.describe(ids)).assets : [];
    const fields = described.map(({ attributes: _a, attributes_truncated: _t, profiling_attributes_omitted: _p, ...f }: Json) => f);
    return this.stamp({ api_asset_id: assetId, fields, total: rel.total, offset: rel.offset, has_more: rel.has_more, next_offset: rel.next_offset });
  }

  /** One call for the agent: search, rank, describe the best matches and list their related assets. */
  async lookup(text: string, top = 5, related = 3): Promise<Json> {
    const n = clamp(top, 1, 10, 5);
    if (this.env.access_mode === "token") {
      return this.api<Json>(`lookup?${new URLSearchParams({ q: text, top: String(n) })}`);
    }
    const found = await this.search(text, "ANYWHERE", 25, 0);
    const ranked: Json[] = found.results;
    const best = ranked.slice(0, n);
    const described: Record<string, Json> = {};
    if (best.length) for (const d of (await this.describe(best.map((a) => a.id))).assets) described[d.id] = d;
    const links = async (id: string): Promise<Json[]> => {
      try {
        const [out, inc] = await Promise.all([this.relations(id, "outgoing", 10), this.relations(id, "incoming", 10)]);
        return [
          ...out.results.filter((r: Json) => r.target).map((r: Json) => ({ direction: "outgoing", ...r.target })),
          ...inc.results.filter((r: Json) => r.source).map((r: Json) => ({ direction: "incoming", ...r.source })),
        ].slice(0, 15);
      } catch {
        return [];
      }
    };
    const rel = await Promise.all(best.slice(0, related).map((a) => links(a.id)));
    const matches = best.map((a, i) => {
      const d = described[a.id] ?? {};
      const attributes = (d.attributes ?? []).filter((x: Json) => !["definition", "description"].includes(String(x.type ?? "").toLowerCase()));
      return {
        ...a, definition: d.definition ?? null, attributes: attributes.slice(0, 8), score: rankScore(a, text),
        ...(d.error ? { error: d.error } : {}), ...(i < related ? { related: rel[i] } : {}),
      };
    });
    const other_results = ranked.slice(n).map(({ id, name, display_name, type, status }) => ({ id, name, display_name, type, status }));
    return this.stamp({
      query: text, total: found.total, matches, other_results,
      note: "Ranked: business terms / product fields / accepted assets and readable names first; score is a heuristic.",
    });
  }
}
