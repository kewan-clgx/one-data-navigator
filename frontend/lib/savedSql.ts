/** Saved SQL (the user's SQL repository): API calls, name suggestions and a change event (docs/sql_repository_plan.md). */

export type SqlDialect = "bigquery" | "postgres" | "snowflake" | "mysql" | "oracle" | "duckdb";

export type SavedQueryItem = {
  id: string;
  name: string;
  description: string;
  connection_id: string | null;
  dialect: SqlDialect;
  tables: string[];
  version: number;
  created_at: string;
  updated_at: string;
  snippet: string;
};
export type SavedQuery = SavedQueryItem & { sql: string };
export type SavedQueryVersion = { version: number; sql: string; saved_at: string };

/** The workbench's open file: what Save overwrites, and what "unsaved changes" compares against. */
export type OpenFile = { id: string; name: string; version: number; savedSql: string };

/** Fired after any save, rename or delete, so the Artifacts list and Explorer counts refresh. */
export const SAVED_SQL_CHANGED = "odn:saved-sql-changed";
export const notifySavedSqlChanged = () => window.dispatchEvent(new Event(SAVED_SQL_CHANGED));

export class NameTakenError extends Error {
  suggestion: string;
  constructor(suggestion: string) {
    super("That name is already used by another saved query.");
    this.suggestion = suggestion;
  }
}
export class VersionConflictError extends Error {
  version: number;
  constructor(version: number) {
    super("This query was saved somewhere else (another tab?) since you opened it.");
    this.version = version;
  }
}

const BASE = "/api/odn/saved-queries";

async function send<T>(path: string, init: RequestInit = {}): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    ...init,
    headers: init.body ? { "Content-Type": "application/json" } : undefined,
    cache: "no-store",
  });
  if (r.status === 204) return undefined as T;
  const body = await r.json().catch(() => ({}));
  if (r.status === 409 && body.detail === "name_taken") throw new NameTakenError(body.suggestion);
  if (r.status === 409 && body.detail === "conflict") throw new VersionConflictError(body.version);
  if (!r.ok) throw new Error(typeof body.detail === "string" ? body.detail : `Request failed (${r.status})`);
  return body as T;
}

export const savedSql = {
  list: (params: { q?: string; table?: string; limit?: number } = {}) => {
    const q = new URLSearchParams();
    if (params.q) q.set("q", params.q);
    if (params.table) q.set("table", params.table);
    if (params.limit) q.set("limit", String(params.limit));
    return send<SavedQueryItem[]>(`?${q}`);
  },
  get: (id: string) => send<SavedQuery>(`/${encodeURIComponent(id)}`),
  create: (body: { name: string; sql: string; dialect: SqlDialect; connection_id: string | null; description?: string }) =>
    send<SavedQuery>("", { method: "POST", body: JSON.stringify(body) }),
  update: (id: string, body: { version: number; sql?: string; name?: string; description?: string; dialect?: SqlDialect; connection_id?: string | null }) =>
    send<SavedQuery>(`/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify(body) }),
  remove: (id: string) => send<void>(`/${encodeURIComponent(id)}`, { method: "DELETE" }),
  versions: (id: string) => send<SavedQueryVersion[]>(`/${encodeURIComponent(id)}/versions`),
  downloadUrl: (id: string) => `${BASE}/${encodeURIComponent(id)}/download`,
};

/** A file-style name: lower-case letters, digits, "_" and "-", at most 80 characters. */
export function fileName(text: string): string {
  return text.trim().toLowerCase().replace(/\.sql$/, "").replace(/[^a-z0-9_-]+/g, "_").replace(/^[_-]+|[_-]+$/g, "")
    .slice(0, 80).replace(/[_-]+$/, "");
}

const WORDS = /[A-Za-z_][\w$]*/g;
const SKIP = new Set(["select", "from", "where", "and", "or", "as", "on", "by", "the", "is", "not", "null", "in", "with", "case", "when", "then", "else", "end", "distinct", "asc", "desc", "true", "false"]);

/**
 * Suggests a file name from the SQL itself (no AI): the main table, the grouping columns ("by_naics"), a short
 * filter value ("ca") and "topN" for a ranked LIMIT, e.g. `businesses_by_naics_ca_top10`.
 */
export function suggestName(sql: string, fallbackTable?: string | null): string {
  const flat = sql.replace(/--[^\n]*/g, " ").replace(/\/\*[\s\S]*?\*\//g, " ").replace(/\s+/g, " ");
  const from = /\bfrom\s+[`"[]?([\w.$-]+)[`"\]]?/i.exec(flat)?.[1];
  const table = (from ?? fallbackTable ?? "query").split(".").pop() ?? "query";
  const parts = [table.replace(/^(vw|v|tbl|stg)_/i, "")];
  const group = /\bgroup\s+by\s+(.+?)(?:\border\b|\blimit\b|\bhaving\b|\bqualify\b|$)/i.exec(flat)?.[1];
  if (group) {
    const cols = (group.match(WORDS) ?? []).filter((w) => !SKIP.has(w.toLowerCase()) && !/^\d+$/.test(w)).slice(0, 2);
    if (cols.length) parts.push("by", ...cols.map((c) => c.replace(/_(code|description|desc|id)$/i, "")));
  }
  const value = /\bwhere\b[^']*'([A-Za-z0-9 ]{2,12})'/i.exec(flat)?.[1];
  if (value) parts.push(value.replace(/\s+/g, "_"));
  const limit = /\border\s+by\b.*\blimit\s+(\d{1,4})\s*$/i.exec(flat.trim())?.[1];
  if (limit) parts.push(`top${limit}`);
  else if (/\bcount\s*\(/i.test(flat) && !group) parts.push("count");
  return fileName(parts.join("_")) || `query_${new Date().toISOString().slice(0, 10)}`;
}
