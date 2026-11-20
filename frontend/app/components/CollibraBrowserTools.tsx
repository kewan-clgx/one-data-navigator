"use client";

/**
 * Collibra through the user's own browser session (docs/collibra_int_phase5_plan.md, stopgap until option A is
 * enabled on the Collibra side). The QueryMuse Collibra Bridge extension performs allowlisted read-only GETs; this
 * module talks to it via window.postMessage and registers agent tools that run in the browser (AG-UI frontend
 * tools). Output mirrors the server-side collibra_* tools.
 */

import { useCopilotReadable, useFrontendTool } from "@copilotkit/react-core";
import { useEffect, useState } from "react";

const APP = "querymuse-app";
const BRIDGE = "querymuse-collibra-bridge";
const TIMEOUT_MS = 30000;
const MAX_TEXT = 2000;

type BridgeResult = { ok: true; base_url: string; data: any } | { ok: false; error: string; message: string; login_url?: string };
type Json = Record<string, unknown>;

export type BridgeInfo = { baseUrl: string; version: string } | null;

function bridgeGet(path: string, params: Record<string, string | number> = {}): Promise<BridgeResult> {
  return new Promise((resolve) => {
    const id = crypto.randomUUID();
    const timer = window.setTimeout(() => {
      window.removeEventListener("message", onMessage);
      resolve({ ok: false, error: "timeout", message: "The Collibra bridge did not answer in time." });
    }, TIMEOUT_MS);
    function onMessage(event: MessageEvent) {
      if (event.source !== window || event.data?.source !== BRIDGE || event.data?.id !== id) return;
      window.clearTimeout(timer);
      window.removeEventListener("message", onMessage);
      resolve(event.data.result as BridgeResult);
    }
    window.addEventListener("message", onMessage);
    window.postMessage({ source: APP, type: "get", id, path, params }, window.location.origin);
  });
}

/** The installed bridge extension, if any (it announces itself on load and when pinged). */
export function useCollibraBridge(): BridgeInfo {
  const [info, setInfo] = useState<BridgeInfo>(null);
  useEffect(() => {
    function onMessage(event: MessageEvent) {
      if (event.source !== window || event.data?.source !== BRIDGE || event.data?.type !== "ready") return;
      setInfo({ baseUrl: String(event.data.base_url), version: String(event.data.version) });
    }
    window.addEventListener("message", onMessage);
    window.postMessage({ source: APP, type: "ping" }, window.location.origin);
    return () => window.removeEventListener("message", onMessage);
  }, []);
  return info;
}

// -- shaping (same output as odn.platform.collibra.client) -------------------------------------------------

function plain(value: unknown): unknown {
  if (typeof value !== "string") return value;
  const doc = new DOMParser().parseFromString(value, "text/html");
  const text = (doc.body.textContent ?? "").replace(/\s+/g, " ").trim();
  return text.length <= MAX_TEXT ? text : `${text.slice(0, MAX_TEXT)}…`;
}

const name = (ref: any): string | null => (ref && typeof ref.name === "string" ? ref.name : null);

class CollibraError extends Error {}

async function get(path: string, params: Record<string, string | number> = {}): Promise<{ base: string; data: any }> {
  const r = await bridgeGet(path, params);
  if (!r.ok) throw new CollibraError(r.message + (r.login_url ? ` (${r.login_url})` : ""));
  return { base: r.base_url, data: r.data };
}

function summary(base: string, a: any): Json {
  return {
    id: a.id, name: a.name, display_name: a.displayName ?? null, type: name(a.type), domain: name(a.domain),
    status: name(a.status), url: typeof a.id === "string" ? `${base}/asset/${a.id}` : null,
  };
}

async function page(path: string, params: Record<string, string | number>, limit: number) {
  const { base, data } = await get(path, params);
  if (!Array.isArray(data?.results) || typeof data?.total !== "number") throw new CollibraError("Collibra returned an invalid catalog page.");
  const offset = typeof data.offset === "number" ? data.offset : Number(params.offset ?? 0);
  const results = data.results.slice(0, limit);
  const consumed = offset + results.length;
  const has_more = consumed < data.total && results.length > 0;
  return { base, results, total: data.total, offset, limit, has_more, next_offset: has_more ? consumed : null };
}

const stamp = (base: string, payload: Json): Json => ({
  environment: `browser (${new URL(base).host})`, ...payload, retrieved_at: new Date().toISOString(),
});

const clamp = (n: unknown, lo: number, hi: number, dflt: number) => {
  const v = Number(n);
  return Number.isFinite(v) ? Math.max(lo, Math.min(hi, Math.trunc(v))) : dflt;
};

async function attributes(assetId: string, limit = 100, offset = 0) {
  const p = await page("attributes", { assetId, limit, offset }, limit);
  return { ...p, results: p.results.map((a: any) => ({ type: name(a.type), value: plain(a.value) })) };
}

async function describeOne(assetId: string): Promise<Json> {
  try {
    const { base, data } = await get(`assets/${assetId}`);
    const attrs = await attributes(assetId);
    const definition =
      ["definition", "description"]
        .map((t) => attrs.results.find((a: any) => typeof a.type === "string" && a.type.toLowerCase().includes(t) && a.value))
        .find(Boolean)?.value ?? null;
    return { ...summary(base, data), definition, attributes: attrs.results, attributes_truncated: attrs.has_more };
  } catch (e) {
    return { id: assetId, error: (e as Error).message };
  }
}

async function describe(ids: string[]): Promise<Json[]> {
  const out: Json[] = [];
  for (let i = 0; i < ids.length; i += 4) out.push(...(await Promise.all(ids.slice(i, i + 4).map(describeOne))));
  return out;
}

async function run(fn: () => Promise<Json>): Promise<Json> {
  try {
    return await fn();
  } catch (e) {
    return { error: (e as Error).message };
  }
}

// -- agent tools (run in the browser) -----------------------------------------------------------------------

/** Registers the collibra_browser_* tools while the bridge extension is present, and tells the agent about it. */
export function CollibraBrowserTools() {
  const bridge = useCollibraBridge();
  const available = bridge ? "enabled" : "disabled";

  useCopilotReadable({
    description: "Collibra access through the user's browser",
    value: bridge
      ? `The QueryMuse Collibra Bridge extension is installed for ${bridge.baseUrl}: the collibra_browser_* tools ` +
        "read Collibra with the user's own Collibra browser session and permissions."
      : "No Collibra browser bridge is installed.",
  });

  useFrontendTool(
    {
      name: "collibra_browser_status",
      description: "Checks whether the user is signed in to Collibra in this browser (via the Collibra bridge extension).",
      available,
      parameters: [],
      handler: async () =>
        run(async () => {
          const r = await bridgeGet("users/current");
          if (!r.ok) return { signed_in: false, detail: r.message, login_url: r.login_url ?? null };
          return stamp(r.base_url, { signed_in: true, username: r.data?.userName ?? null });
        }),
    },
    [available],
  );

  useFrontendTool(
    {
      name: "collibra_browser_search_assets",
      description:
        "Searches Collibra assets by name with the user's own Collibra session (browser bridge). Use short name " +
        "fragments and synonyms. Returns results (id, name, type, domain, status, url) and paging fields.",
      available,
      parameters: [
        { name: "text", type: "string", description: "Name text to search for", required: true },
        { name: "match", type: "string", description: "ANYWHERE (default), START or EXACT", required: false },
        { name: "limit", type: "number", description: "Results per page, 1-50 (default 10)", required: false },
        { name: "offset", type: "number", description: "Paging offset (next_offset of the previous page)", required: false },
      ],
      handler: async ({ text, match, limit, offset }: { text: string; match?: string; limit?: number; offset?: number }) =>
        run(async () => {
          const lim = clamp(limit, 1, 50, 10);
          const mode = ["ANYWHERE", "START", "EXACT"].includes(String(match)) ? String(match) : "ANYWHERE";
          const p = await page("assets", { name: String(text), nameMatchMode: mode, limit: lim, offset: clamp(offset, 0, 100000, 0) }, lim);
          return stamp(p.base, { ...p, base: undefined, results: p.results.map((a: any) => summary(p.base, a)) });
        }),
    },
    [available],
  );

  useFrontendTool(
    {
      name: "collibra_browser_describe_assets",
      description:
        "Reads definitions and attributes of up to 20 Collibra assets (ids from search or relations) with the user's " +
        "own Collibra session. Returns assets with definition (plain text) and attributes, or a per-asset error.",
      available,
      parameters: [{ name: "asset_ids", type: "string[]", description: "1-20 Collibra asset ids", required: true }],
      handler: async ({ asset_ids }: { asset_ids: string[] }) =>
        run(async () => {
          const ids = Array.from(new Set((asset_ids ?? []).map(String))).slice(0, 20);
          if (!ids.length) return { error: "give 1-20 asset ids" };
          const assets = await describe(ids);
          return { assets, retrieved_at: new Date().toISOString() };
        }),
    },
    [available],
  );

  useFrontendTool(
    {
      name: "collibra_browser_get_relations",
      description:
        "Follows Collibra relations (lineage, term-to-column, table-to-column) of one asset with the user's own " +
        "Collibra session. direction: outgoing (asset is the source) or incoming.",
      available,
      parameters: [
        { name: "asset_id", type: "string", description: "The asset id", required: true },
        { name: "direction", type: "string", description: "outgoing (default) or incoming", required: false },
        { name: "limit", type: "number", description: "Results per page, 1-100 (default 20)", required: false },
        { name: "offset", type: "number", description: "Paging offset", required: false },
      ],
      handler: async ({ asset_id, direction, limit, offset }: { asset_id: string; direction?: string; limit?: number; offset?: number }) =>
        run(async () => {
          const lim = clamp(limit, 1, 100, 20);
          const key = direction === "incoming" ? "targetId" : "sourceId";
          const p = await page("relations", { [key]: String(asset_id), limit: lim, offset: clamp(offset, 0, 100000, 0) }, lim);
          const ref = (r: any) => (r ? { id: r.id, name: r.name ?? r.displayName ?? null, url: `${p.base}/asset/${r.id}` } : null);
          const results = p.results.map((r: any) => ({ relation_type_id: r.type?.id ?? null, source: ref(r.source), target: ref(r.target) }));
          return stamp(p.base, { asset_id, direction: key === "targetId" ? "incoming" : "outgoing", ...p, base: undefined, results });
        }),
    },
    [available],
  );

  useFrontendTool(
    {
      name: "collibra_browser_get_api_fields",
      description:
        "Lists the fields linked from a Collibra table/API asset with each field's definition (one page), using the " +
        "user's own Collibra session. Relations are catalog candidates, not proof of schema membership.",
      available,
      parameters: [
        { name: "asset_id", type: "string", description: "The table or API asset id", required: true },
        { name: "limit", type: "number", description: "Fields per page, 1-20 (default 20)", required: false },
        { name: "offset", type: "number", description: "Paging offset", required: false },
      ],
      handler: async ({ asset_id, limit, offset }: { asset_id: string; limit?: number; offset?: number }) =>
        run(async () => {
          const lim = clamp(limit, 1, 20, 20);
          const p = await page("relations", { sourceId: String(asset_id), limit: lim, offset: clamp(offset, 0, 100000, 0) }, lim);
          const ids = p.results.map((r: any) => r.target?.id).filter((x: unknown): x is string => typeof x === "string");
          const fields = (await describe(ids)).map(({ attributes: _a, attributes_truncated: _t, display_name: _d, ...f }) => f);
          return stamp(p.base, { api_asset_id: asset_id, fields, total: p.total, offset: p.offset, has_more: p.has_more, next_offset: p.next_offset });
        }),
    },
    [available],
  );

  return null;
}
