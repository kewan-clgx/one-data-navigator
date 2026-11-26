"use client";

import { useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { createPortal } from "react-dom";

import CatalogSearchResults, {
  isSnapshot,
  pathKey,
  prefsOf,
  searchIndex,
  StarButton,
  tablePriority,
  withoutSnapshots,
  type MyTables,
  type SearchIndex,
} from "./CatalogSearch";
import { CONNECTIONS_CHANGED } from "@/lib/client-api";
import { connectionFor, isWorkspaceSql, tablesIn } from "@/lib/sqlRefs";

import DictionaryPanel from "./DictionaryPanel";
import { DIALECT_LABEL, type EditorStatus, type SqlSchema } from "./SqlEditor";
import { dialectFor, formatSql } from "@/lib/sqlFormat";
import { CODE_THEMES, setCodeTheme, useCodeTheme, type CodeThemeId } from "@/lib/displayPrefs";

// The editor (CodeMirror) loads only when the workbench opens, keeping the chat bundle light.
const SqlEditor = dynamic(() => import("./SqlEditor"), {
  ssr: false,
  loading: () => <div className="sqled-editor sqled-loading">Loading editor…</div>,
});
const WRAP_KEY = "odn.sql.wrap";

/** The table the user picked (or every table of SQL opened from the chat); the chat is scoped to it. */
export type TableScope = { connectionId: string; connectionName: string; path: string[]; others?: string[][] };

/** SQL sent from a chat answer to the workbench; `nonce` makes repeated clicks reopen it. */
export type SqlRequest = { sql: string; nonce: number };

type WorkspaceArtifact = {
  artifact: { name: string; row_count: number; truncated: boolean; columns: { name: string; type: string; nullable: boolean }[] };
  preview: Record<string, unknown>[];
  duration_ms: number;
};

/** The connection shown in Explorer; `chosen` is false while it is only the default (first) connection. */
export type ActiveConnection = { id: string; name: string; kind: string; chosen: boolean };

type CuratedRow = { table_path: string; field_path: string; definition: string; status: string };

/** Curated field definitions become searchable alongside source descriptions. */
function withCuratedDefinitions(index: SearchIndex, defs: CuratedRow[]): SearchIndex {
  if (!defs.length) return index;
  const byKey = new Map(defs.filter((d) => d.status !== "deprecated").map((d) => [`${d.table_path}|${d.field_path}`, d.definition]));
  return {
    ...index,
    datasets: index.datasets.map((ds) => ({
      ...ds,
      tables: ds.tables.map((t) => ({
        ...t,
        columns: t.columns.map((c) => {
          const curated = byKey.get(`${t.path.join(".")}|${c.name}`);
          return curated ? { ...c, description: c.description ? `${curated} — ${c.description}` : curated } : c;
        }),
      })),
    })),
  };
}

const HIDE_SNAPSHOTS_KEY = "odn.explorer.hideSnapshots";
const CONNECTION_KEY = "odn.explorer.connection";
const MY_TABLES_SHOWN = 8;
const PREVIEW_ROWS = 20;
// BigQuery checks the bytes-billed cap against the UNSAMPLED size of the columns read, so avoid SELECT *.
const PREVIEW_COLUMNS = 10;
// Below this size a full read is cheap, and block sampling could return no rows at all.
const PREVIEW_SAMPLE_MIN_BYTES = 1024 ** 3;

function previewSql(s: TableSchema, focus: string | null, connectionKind: string = "bigquery"): string {
  const names = s.columns.map((c) => c.name).filter((n) => ["snowflake", "mysql", "oracle"].includes(connectionKind) || !n.includes("."));
  const cols = (focus && names.includes(focus) ? [focus, ...names.filter((n) => n !== focus)] : names).slice(
    0,
    PREVIEW_COLUMNS,
  );
  
  // Dialect-specific SQL generation
  if (connectionKind === "oracle") {
    const quote = (part: string) => `"${part.replaceAll('"', '""')}"`;
    return `SELECT ${cols.map(quote).join(", ")}\nFROM ${s.path.map(quote).join(".")}\nFETCH FIRST ${PREVIEW_ROWS} ROWS ONLY`;
  } else if (connectionKind === "snowflake") {
    const quote = (part: string) => `"${part.replaceAll('"', '""')}"`;
    return `SELECT ${cols.map(quote).join(", ")}\nFROM ${s.path.map(quote).join(".")}\nLIMIT ${PREVIEW_ROWS}`;
  } else if (connectionKind === "mysql") {
    const quote = (part: string) => `\`${part.replaceAll("`", "``")}\``;
    return `SELECT ${cols.map(quote).join(", ")}\nFROM ${s.path.map(quote).join(".")}\nLIMIT ${PREVIEW_ROWS}`;
  } else if (connectionKind === "postgres") {
    // PostgreSQL: use quoted identifiers and LIMIT (no TABLESAMPLE)
    const tablePath = s.path.map(p => `"${p}"`).join(".");
    return `SELECT ${cols.join(", ")}\nFROM ${tablePath}\nLIMIT ${PREVIEW_ROWS}`;
  } else {
    // BigQuery (default): use backticks and optional TABLESAMPLE for large tables
    const sample = (s.size_bytes ?? Infinity) > PREVIEW_SAMPLE_MIN_BYTES ? " TABLESAMPLE SYSTEM (1 PERCENT)" : "";
    return `SELECT ${cols.join(", ")}\nFROM \`${s.path.join(".")}\`${sample}\nLIMIT ${PREVIEW_ROWS}`;
  }
}

type Connection = { id: string; name: string; kind: string; scope?: "shared" | "personal"; allowed_objects: string[] };
type CatalogNode = { kind: string; path: string[]; name: string };
type Column = { name: string; type: string; nullable: boolean; description?: string | null };
type TableSchema = { path: string[]; kind: "table" | "view"; columns: Column[]; row_count?: number | null; size_bytes?: number | null; description?: string | null };
type QueryMeta = { rows: number; bytes_processed: number | null; duration_ms: number; truncated: boolean; columns: Column[] };
type QueryResult = { meta: QueryMeta; tables: string[]; bytes_estimated: number | null; rows: Record<string, unknown>[] };

type Outcome =
  | { kind: "result"; result: QueryResult & { savedAs?: string } }
  | { kind: "confirm"; bytes: number }
  | { kind: "error"; message: string };

class ApiError extends Error {
  constructor(public status: number, public detail: unknown) {
    super(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api/odn/${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, body.detail ?? `Request failed (HTTP ${res.status})`);
  return body as T;
}

function query(params: Record<string, string[]>): string {
  const q = new URLSearchParams();
  for (const [k, vs] of Object.entries(params)) vs.forEach((v) => q.append(k, v));
  return q.toString();
}

function formatBytes(n: number | null | undefined): string {
  if (n == null) return "–";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v < 10 && i > 0 ? 1 : 0)} ${units[i]}`;
}

function cell(v: unknown): string {
  if (v === null || v === undefined) return "NULL";
  return typeof v === "object" ? JSON.stringify(v) : String(v);
}

export default function Explorer({
  scope,
  onScopeChange,
  onConnectionChange,
  workbenchHost,
  workbenchOpen,
  onWorkbenchOpenChange,
  sqlRequest,
  sessionId,
}: {
  scope: TableScope | null;
  onScopeChange: (scope: TableScope | null) => void;
  onConnectionChange?: (connection: ActiveConnection | null) => void;
  /** Element in the chat pane that the SQL workbench overlay renders into. */
  workbenchHost: HTMLElement | null;
  workbenchOpen: boolean;
  onWorkbenchOpenChange: (open: boolean) => void;
  sqlRequest?: SqlRequest | null;
  /** The conversation's workspace, for SQL over its results (q1, q2, ...). */
  sessionId?: string;
}) {
  const [connections, setConnections] = useState<Connection[]>([]);
  const [connectionId, setConnectionId] = useState<string>("");
  const [datasets, setDatasets] = useState<CatalogNode[]>([]);
  const [tables, setTables] = useState<Record<string, CatalogNode[]>>({});
  const [search, setSearch] = useState("");
  const deferredSearch = useDeferredValue(search);
  const [index, setIndex] = useState<SearchIndex | null>(null);
  const [indexState, setIndexState] = useState<"loading" | "ready" | "error">("loading");
  const [hideSnapshots, setHideSnapshots] = useState(true);
  const [mine, setMine] = useState<MyTables | null>(null);
  const [schema, setSchema] = useState<TableSchema | null>(null);
  const [focusColumn, setFocusColumn] = useState<string | null>(null);
  const [role, setRole] = useState<string | null>(null);
  const [sql, setSql] = useState("");
  const [editorStatus, setEditorStatus] = useState<EditorStatus | null>(null);
  // Wrap is on by default: the workbench is narrow and long `project.dataset.table` paths are common.
  const codeTheme = useCodeTheme();
  const [wrap, setWrap] = useState(() => typeof window === "undefined" || localStorage.getItem(WRAP_KEY) !== "0");
  const [tall, setTall] = useState(false);
  const [editorNote, setEditorNote] = useState<string | null>(null);
  // What the last Run sent (the selection or the whole editor), so "Run anyway" repeats exactly that.
  const [lastRunText, setLastRunText] = useState<string | null>(null);
  // "workspace": the SQL reads this conversation's results (DuckDB), not the connection's warehouse.
  const [target, setTarget] = useState<"connection" | "workspace">("connection");
  const [pending, setPending] = useState<{ sql: string; tables: string[][]; connectionId: string } | null>(null);
  const [running, setRunning] = useState(false);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (localStorage.getItem(HIDE_SNAPSHOTS_KEY) === "false") setHideSnapshots(false);
  }, []);

  const toggleSnapshots = () => {
    setHideSnapshots((v) => {
      localStorage.setItem(HIDE_SNAPSHOTS_KEY, String(!v));
      return !v;
    });
  };

  const loadConnections = useCallback(() => {
    api<Connection[]>("connections")
      .then((cs) => {
        setConnections(cs);
        // Keep the current selection if it still exists (e.g. after adding a connection in Settings),
        // else restore the user's last explicit choice, else default to the first connection.
        const stored = localStorage.getItem(CONNECTION_KEY);
        setConnectionId((id) =>
          cs.some((c) => c.id === id) ? id : stored && cs.some((c) => c.id === stored) ? stored : cs[0]?.id ?? "",
        );
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  useEffect(() => {
    const c = connections.find((x) => x.id === connectionId);
    onConnectionChange?.(
      c
        ? {
            id: c.id,
            name: c.name,
            kind: c.kind,
            chosen: connections.length === 1 || localStorage.getItem(CONNECTION_KEY) === c.id,
          }
        : null,
    );
  }, [connectionId, connections, onConnectionChange]);

  const chooseConnection = (id: string) => {
    localStorage.setItem(CONNECTION_KEY, id);
    setConnectionId(id);
  };

  useEffect(() => {
    loadConnections();
    window.addEventListener(CONNECTIONS_CHANGED, loadConnections);
    return () => window.removeEventListener(CONNECTIONS_CHANGED, loadConnections);
  }, [loadConnections]);

  const loadMine = useCallback(() => {
    if (!connectionId) return;
    api<MyTables>(`connections/${connectionId}/my-tables`)
      .then(setMine)
      .catch(() => setMine(null));
  }, [connectionId]);

  useEffect(() => {
    if (!connectionId) return;
    setDatasets([]);
    setTables({});
    setSchema(null);
    onScopeChange(null);
    setMine(null);
    loadMine();
    api<CatalogNode[]>(`connections/${connectionId}/catalog`)
      .then(setDatasets)
      .catch((e: Error) => setError(e.message));
    let cancelled = false;
    setIndex(null);
    setIndexState("loading");
    Promise.all([
      api<SearchIndex>(`connections/${connectionId}/search-index`),
      api<CuratedRow[]>(`connections/${connectionId}/definitions`).catch(() => [] as CuratedRow[]),
    ])
      .then(([idx, defs]) => {
        if (cancelled) return;
        setIndex(withCuratedDefinitions(idx, defs));
        setIndexState("ready");
      })
      .catch(() => !cancelled && setIndexState("error"));
    return () => {
      cancelled = true;
    };
  }, [connectionId, loadMine, onScopeChange]);

  const caseSensitive = ["snowflake", "mysql", "oracle"].includes(connections.find((c) => c.id === connectionId)?.kind ?? "");
  const tablePathParts = ["mysql", "oracle"].includes(connections.find((c) => c.id === connectionId)?.kind ?? "") ? 2 : 3;
  const prefs = useMemo(() => prefsOf(mine, caseSensitive), [mine, caseSensitive]);

  // Stored paths are lowercase (matching is case-insensitive); map back to the catalog's spelling.
  const realPaths = useMemo(() => {
    const m = new Map<string, string[]>();
    datasets.forEach((d) => m.set(pathKey(d.path, caseSensitive), d.path));
    index?.datasets.forEach((d) => {
      m.set(pathKey(d.path, caseSensitive), d.path);
      d.tables.forEach((t) => m.set(pathKey(t.path, caseSensitive), t.path));
    });
    return m;
  }, [datasets, index, caseSensitive]);
  const resolvePath = useCallback((key: string) => realPaths.get(key) ?? key.split("."), [realPaths]);

  const toggleFavorite = useCallback(
    async (path: string[]) => {
      const key = pathKey(path, caseSensitive);
      const on = prefs.favorites.has(key);
      setMine((m) =>
        m && {
          ...m,
          favorites: on
            ? m.favorites.filter((f) => f.path !== key)
            : [{ path: key, kind: path.length >= tablePathParts ? "table" : "dataset" }, ...m.favorites],
        },
      );
      try {
        if (on) {
          await api(`connections/${connectionId}/favorites?${query({ path })}`, { method: "DELETE" });
        } else {
          await api(`connections/${connectionId}/favorites`, { method: "PUT", body: JSON.stringify({ path }) });
        }
      } catch (e) {
        setError((e as Error).message);
      }
      loadMine();
    },
    [connectionId, prefs, loadMine, caseSensitive, tablePathParts],
  );

  const datasetRank = useCallback(
    (ds: CatalogNode) => {
      const key = pathKey(ds.path, caseSensitive);
      const inside = (k: string) => k.startsWith(`${key}.`);
      if (prefs.favorites.has(key) || [...prefs.favorites].some(inside)) return 2;
      return [...prefs.recent.keys()].some(inside) ? 1 : 0;
    },
    [prefs, caseSensitive],
  );
  const sortedDatasets = useMemo(
    () => [...datasets].sort((a, b) => datasetRank(b) - datasetRank(a)),
    [datasets, datasetRank],
  );

  const terms = useMemo(
    () => deferredSearch.trim().toLowerCase().split(/\s+/).filter(Boolean),
    [deferredSearch],
  );
  // Snapshots are removed before matching, so they are neither shown nor searched.
  const visibleIndex = useMemo(
    () => (index && hideSnapshots ? withoutSnapshots(index) : index),
    [index, hideSnapshots],
  );
  const snapshotCount = useMemo(
    () => index?.datasets.reduce((n, d) => n + d.tables.filter((t) => isSnapshot(t.name)).length, 0) ?? 0,
    [index],
  );
  const results = useMemo(
    () => (visibleIndex && terms.length ? searchIndex(visibleIndex, terms, prefs) : null),
    [visibleIndex, terms, prefs],
  );

  useEffect(() => {
    api<{ role: string }>("me")
      .then((me) => setRole(me.role))
      .catch(() => setRole(null));
  }, []);

  const toggleDataset = useCallback(
    async (ds: CatalogNode) => {
      const key = ds.path.join(".");
      if (tables[key]) {
        const { [key]: _, ...rest } = tables;
        setTables(rest);
        return;
      }
      try {
        const nodes = await api<CatalogNode[]>(`connections/${connectionId}/catalog?${query({ parent: ds.path })}`);
        setTables((t) => ({ ...t, [key]: nodes }));
      } catch (e) {
        setError((e as Error).message);
      }
    },
    [connectionId, tables],
  );

  const openTable = useCallback(
    async (path: string[], column?: string, fromSql?: { others: string[][] }) => {
      try {
        const s = await api<TableSchema>(`connections/${connectionId}/schema?${query({ path })}`);
        setSchema(s);
        setFocusColumn(column ?? null);
        const conn = connections.find((c) => c.id === connectionId);
        onScopeChange({
          connectionId, connectionName: conn?.name ?? connectionId, path: s.path,
          ...(fromSql?.others.length ? { others: fromSql.others } : {}),
        });
        if (fromSql) return; // keep the SQL that was opened from the chat
        setTarget("connection");
        const names = s.columns.map((c) => c.name).filter((n) => ["snowflake", "mysql", "oracle"].includes(conn?.kind ?? "") || !n.includes("."));
        const picked = column && names.includes(column) ? [column, ...names.filter((n) => n !== column)] : names;
        if (conn?.kind === "oracle") {
          const quote = (part: string) => `"${part.replaceAll('"', '""')}"`;
          setSql(`SELECT ${picked.slice(0, 5).map(quote).join(", ")}\nFROM ${path.map(quote).join(".")}\nFETCH FIRST 100 ROWS ONLY`);
        } else if (conn?.kind === "snowflake") {
          const quote = (part: string) => `"${part.replaceAll('"', '""')}"`;
          setSql(`SELECT ${picked.slice(0, 5).map(quote).join(", ")}\nFROM ${path.map(quote).join(".")}\nLIMIT 100`);
        } else if (conn?.kind === "mysql") {
          const quote = (part: string) => `\`${part.replaceAll("`", "``")}\``;
          setSql(`SELECT ${picked.slice(0, 5).map(quote).join(", ")}\nFROM ${path.map(quote).join(".")}\nLIMIT 100`);
        } else {
          setSql(`SELECT ${picked.slice(0, 5).join(", ")}\nFROM \`${path.join(".")}\`\nLIMIT 100`);
        }
        setOutcome(null);
      } catch (e) {
        setError((e as Error).message);
      }
    },
    [connectionId, connections, onScopeChange],
  );

  // Clearing the scope chip in the chat also clears the selection here.
  useEffect(() => {
    if (!scope) {
      setSchema(null);
      setFocusColumn(null);
    }
  }, [scope]);

  const workbenchRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (workbenchOpen) workbenchRef.current?.focus();
  }, [workbenchOpen]);

  const run = useCallback(
    async (confirmed: boolean, sqlText: string = sql) => {
      setRunning(true);
      setOutcome(null);
      try {
        if (target === "workspace") {
          // DuckDB over this conversation's saved results: no warehouse scan, and like every workbench run, no AI.
          const out = await api<WorkspaceArtifact>(`workspace/${encodeURIComponent(sessionId ?? "")}/query`, {
            method: "POST",
            body: JSON.stringify({ sql: sqlText }),
          });
          setOutcome({
            kind: "result",
            result: {
              meta: {
                rows: out.artifact.row_count, bytes_processed: null, duration_ms: out.duration_ms,
                truncated: out.artifact.truncated, columns: out.artifact.columns,
              },
              tables: [], bytes_estimated: null, rows: out.preview,
              savedAs: out.artifact.name,
            },
          });
          return;
        }
        const result = await api<QueryResult>(`connections/${connectionId}/query`, {
          method: "POST",
          body: JSON.stringify({ sql: sqlText, confirmed, source: "explorer", max_rows: 1000 }),
        });
        setOutcome({ kind: "result", result });
        loadMine();
      } catch (e) {
        const err = e as ApiError;
        const detail = err.detail as Record<string, unknown> | string;
        if (err.status === 409 && typeof detail === "object" && detail.confirmation_required) {
          setOutcome({ kind: "confirm", bytes: Number(detail.bytes_estimated) });
        } else if (target === "workspace" && typeof detail === "object" && /not in allowlist/.test(String(detail.rejected))) {
          setOutcome({
            kind: "error",
            message:
              "This conversation's result tables (q1, q2, …) are no longer available; they're kept for a limited time. " +
              "Ask the question again in the chat, or rewrite the SQL against the warehouse tables.",
          });
        } else if (typeof detail === "object" && (detail.rejected || detail.failed)) {
          setOutcome({ kind: "error", message: String(detail.rejected ?? detail.failed) });
        } else {
          setOutcome({ kind: "error", message: err.message });
        }
      } finally {
        setRunning(false);
      }
    },
    [connectionId, sql, loadMine, target, sessionId],
  );

  const tableHitCount = results?.reduce((n, d) => n + d.hits.length, 0) ?? 0;
  const fieldHitCount = results?.reduce((n, d) => n + d.hits.reduce((m, h) => m + h.columnHits.length, 0), 0) ?? 0;
  const indexedTables = visibleIndex?.datasets.reduce((n, d) => n + d.tables.length, 0) ?? 0;
  const hiddenNote = hideSnapshots && snapshotCount ? ` · ${snapshotCount} snapshots hidden` : "";
  const status = results
    ? `${tableHitCount} ${tableHitCount === 1 ? "table" : "tables"} match` +
      (fieldHitCount ? `, ${fieldHitCount} matching ${fieldHitCount === 1 ? "field" : "fields"}` : "") +
      hiddenNote
    : indexState === "loading"
      ? "Indexing datasets, tables and fields…"
      : indexState === "error"
        ? "Search is unavailable; browse the tree below."
        : index?.status === "partial"
          ? `${indexedTables} tables indexed; some datasets could not be indexed.${hiddenNote}`
          : `${indexedTables} tables searchable${hiddenNote}`;
  const selectedPath = schema?.path.join(".") ?? null;

  const sidebar = (
      <aside className="sidebar" aria-label="Schema explorer">
        {error && (
          <p role="alert" className="error">
            {error}{" "}
            <button type="button" className="link" onClick={() => setError(null)}>Dismiss</button>
          </p>
        )}
        <label>
          Connection
          <select value={connectionId} onChange={(e) => chooseConnection(e.target.value)}>
            {connections.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name} ({c.kind}{c.scope === "personal" ? ", yours" : ""})
              </option>
            ))}
          </select>
        </label>
        {connections.length === 0 && !error && (
          <p className="muted">No data sources yet. Add one in Settings → Data sources.</p>
        )}
        <div className="search-row">
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => e.key === "Escape" && setSearch("")}
            placeholder="Search datasets, tables and fields"
            aria-label="Search datasets, tables and fields"
            disabled={indexState === "error"}
            aria-describedby="search-status"
          />
          <button
            type="button"
            role="switch"
            className="switch"
            aria-checked={hideSnapshots}
            onClick={toggleSnapshots}
            title="Hide timestamped snapshot tables (e.g. *_202610222329, *_20261022) from the tree and search"
          >
            <span aria-hidden className="switch-track">
              <span className="switch-thumb" />
            </span>
            Hide snapshots
          </button>
        </div>
        <p id="search-status" role="status" className="muted search-status">
          {status}
        </p>
        {results ? (
          results.length ? (
            <CatalogSearchResults
              results={results}
              terms={terms}
              selectedPath={selectedPath}
              onOpen={openTable}
              prefs={prefs}
              onToggleFavorite={toggleFavorite}
            />
          ) : (
            <p className="muted">No datasets, tables or fields match “{deferredSearch.trim()}”.</p>
          )
        ) : (
        <>
        {mine && (mine.favorites.length > 0 || mine.recent.length > 0) && (
          <section className="my-tables" aria-label="My tables">
            {mine.favorites.length > 0 && (
              <>
                <h3>Favorites</h3>
                <ul className="tree">
                  {mine.favorites.map((f) => {
                    const path = resolvePath(f.path);
                    const name = path.slice(f.kind === "table" ? -2 : -1).join(".");
                    return (
                      <li key={f.path} className="row">
                        <button
                          type="button"
                          title={path.join(".")}
                          className={selectedPath === path.join(".") ? "selected" : ""}
                          onClick={() =>
                            f.kind === "table" ? openTable(path) : setSearch(path[path.length - 1])
                          }
                        >
                          {f.kind === "table" ? "▦" : "▸"} {name}
                        </button>
                        <StarButton path={path} label={name} prefs={prefs} onToggle={toggleFavorite} />
                      </li>
                    );
                  })}
                </ul>
              </>
            )}
            {mine.recent.length > 0 && (
              <>
                <h3>Recently queried</h3>
                <ul className="tree">
                  {mine.recent.slice(0, MY_TABLES_SHOWN).map((r) => {
                    const path = resolvePath(r.path);
                    const name = path.slice(-2).join(".");
                    return (
                      <li key={r.path} className="row">
                        <button
                          type="button"
                          title={`${path.join(".")} · queried ${r.query_count}×, last ${new Date(r.last_used_at).toLocaleString()}`}
                          className={selectedPath === path.join(".") ? "selected" : ""}
                          onClick={() => openTable(path)}
                        >
                          ▦ {name}
                        </button>
                        <StarButton path={path} label={name} prefs={prefs} onToggle={toggleFavorite} />
                      </li>
                    );
                  })}
                </ul>
              </>
            )}
            <h3>All datasets</h3>
          </section>
        )}
        <ul className="tree">
          {sortedDatasets.map((ds) => {
            const key = ds.path.join(".");
            const open = Boolean(tables[key]);
            return (
              <li key={key}>
                <div className="row">
                  <button type="button" aria-expanded={open} onClick={() => toggleDataset(ds)}>
                    {open ? "▾" : "▸"} {ds.name}
                  </button>
                  <StarButton path={ds.path} label={ds.name} prefs={prefs} onToggle={toggleFavorite} />
                </div>
                {open && (
                  <ul>
                    {tables[key]
                      .filter((t) => !hideSnapshots || !isSnapshot(t.name))
                      .map((t, i) => ({ t, i, p: tablePriority(prefs, t.path) }))
                      .sort((a, b) => b.p - a.p || a.i - b.i)
                      .map(({ t }) => (
                      <li key={t.path.join(".")} className="row">
                        <button
                          type="button"
                          title={t.name}
                          className={selectedPath === t.path.join(".") ? "selected" : ""}
                          onClick={() => openTable(t.path)}
                        >
                          {t.kind === "view" ? "◇" : "▦"} {t.name}
                        </button>
                        <StarButton path={t.path} label={t.name} prefs={prefs} onToggle={toggleFavorite} />
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            );
          })}
        </ul>
        </>
        )}
      </aside>
  );

  // SQL from a chat answer: pick the connection whose allowlist covers its tables. The switch resets the
  // explorer (effect above), so the SQL is applied in the next effect, after that reset.
  const lastRequest = useRef<number | null>(null);
  useEffect(() => {
    if (!sqlRequest || sqlRequest.nonce === lastRequest.current || !connections.length) return;
    lastRequest.current = sqlRequest.nonce;
    const tables = tablesIn(sqlRequest.sql);
    onWorkbenchOpenChange(true);
    if (isWorkspaceSql(tables)) {
      setPending(null);
      setTarget("workspace");
      setSql(sqlRequest.sql);
      setOutcome(null);
      return;
    }
    const id = connectionFor(tables, connections, connectionId);
    if (id !== connectionId) chooseConnection(id);
    setPending({ sql: sqlRequest.sql, tables, connectionId: id });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sqlRequest, connections]);

  useEffect(() => {
    if (!pending || pending.connectionId !== connectionId) return;
    setPending(null);
    setTarget("connection");
    setSql(pending.sql);
    setOutcome(null);
    const [first, ...others] = pending.tables;
    if (first) void openTable(first, undefined, { others });
  }, [pending, connectionId, openTable]);

  const runsOn =
    target === "workspace"
      ? "Runs on this conversation's result tables (q1, q2, …), no warehouse scan."
      : `Runs on ${connections.find((c) => c.id === connectionId)?.name ?? "the selected connection"}, read-only and checked against its allowlist.`;

  const activeConnection = connections.find((c) => c.id === connectionId);
  const activeConnectionName = activeConnection?.name ?? "no connection";
  const dialect = dialectFor(activeConnection?.kind, target === "workspace");
  // Completion: the opened table's columns, plus the other tables the chat's SQL referenced.
  const editorSchema = useMemo<SqlSchema>(
    () => ({
      tables: [
        ...(schema ? [{ path: schema.path, columns: schema.columns.map((c) => ({ name: c.name, type: c.type })) }] : []),
        ...(scope?.others ?? []).map((path) => ({ path, columns: [] })),
      ],
    }),
    [schema, scope],
  );

  const flash = (text: string) => {
    setEditorNote(text);
    window.setTimeout(() => setEditorNote(null), 2500);
  };
  function runText(text: string) {
    setLastRunText(text);
    void run(false, text);
  }
  function formatEditor() {
    try {
      setSql(formatSql(sql, dialect));
      flash("Formatted");
    } catch {
      flash(`Couldn't format: the SQL doesn't parse as ${DIALECT_LABEL[dialect]}`);
    }
  }
  function copySql() {
    void navigator.clipboard?.writeText(sql).then(() => flash("Copied to clipboard"));
  }
  function downloadSql() {
    const url = URL.createObjectURL(new Blob([sql], { type: "application/sql" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `${(schema?.path[schema.path.length - 1] ?? "query").replace(/[^\w.-]+/g, "_")}.sql`;
    a.click();
    URL.revokeObjectURL(url);
  }

  const workbenchBody = (
      <>
        <section aria-label="SQL" className="sqled">
          <div className="sqled-toolbar" role="toolbar" aria-label="SQL editor">
            <span className="sqled-chip" title={runsOn}>
              {DIALECT_LABEL[dialect]}
              <span className="sqled-chip-sub">{target === "workspace" ? "conversation results" : activeConnectionName}</span>
            </span>
            <span className="sqled-tools">
              <button type="button" className="sqled-btn" onClick={formatEditor} disabled={!sql.trim()} title="Format SQL (Shift+Alt+F)">
                Format
              </button>
              <button
                type="button"
                className="sqled-btn"
                aria-pressed={wrap}
                onClick={() => {
                  localStorage.setItem(WRAP_KEY, wrap ? "0" : "1");
                  setWrap(!wrap);
                }}
                title="Wrap long lines"
              >
                Wrap
              </button>
              <button type="button" className="sqled-btn" onClick={copySql} disabled={!sql.trim()} title="Copy SQL">
                Copy
              </button>
              <button type="button" className="sqled-btn" onClick={downloadSql} disabled={!sql.trim()} title="Download as .sql">
                .sql
              </button>
              <button type="button" className="sqled-btn" aria-pressed={tall} onClick={() => setTall(!tall)} title={tall ? "Smaller editor" : "Taller editor"}>
                {tall ? "Collapse" : "Expand"}
              </button>
              <select
                className="sqled-theme"
                value={codeTheme}
                onChange={(e) => setCodeTheme(e.target.value as CodeThemeId)}
                aria-label="Editor style"
                title="Editor style (also used for SQL in chat)"
              >
                {CODE_THEMES.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.label}
                  </option>
                ))}
              </select>
            </span>
            <span className="sqled-run">
              {schema && (
                <button
                  type="button"
                  className="sqled-btn"
                  onClick={() => {
                    const conn = connections.find((c) => c.id === connectionId);
                    const text = previewSql(schema, focusColumn, conn?.kind ?? "bigquery");
                    setSql(text);
                    runText(text);
                  }}
                  disabled={running || !connectionId || schema.kind === "view"}
                  title={
                    schema.kind === "view"
                      ? "Sampling isn't available for views; write a filtered query instead"
                      : `Run ${PREVIEW_ROWS} rows of the first ${PREVIEW_COLUMNS} columns`
                  }
                >
                  Preview data
                </button>
              )}
              <button
                type="button"
                className="sqled-run-btn"
                onClick={() => runText(editorStatus?.selection.trim() ? editorStatus.selection : sql)}
                disabled={!sql.trim() || running || (target === "workspace" ? !sessionId : !connectionId)}
                title="Run (⌘/Ctrl+Enter). Runs the selection when text is selected."
              >
                {running ? "Running…" : editorStatus?.selection.trim() ? "▶ Run selection" : "▶ Run"}
                <kbd>⌘↵</kbd>
              </button>
            </span>
          </div>
          <div className={`sqled-frame${tall ? " sqled-frame--tall" : ""}`}>
            <SqlEditor
              id="sql"
              value={sql}
              onChange={setSql}
              onRun={(selection) => runText(selection.trim() ? selection : sql)}
              onFormat={formatEditor}
              onStatus={setEditorStatus}
              dialect={dialect}
              schema={editorSchema}
              wrap={wrap}
              describedBy="sql-note"
            />
          </div>
          <div className="sqled-status" aria-live="polite">
            <span>
              {editorStatus ? `Ln ${editorStatus.line}, Col ${editorStatus.col}` : ""}
              {editorStatus ? ` · ${editorStatus.lines} ${editorStatus.lines === 1 ? "line" : "lines"}` : ""}
              {editorStatus?.selection
                ? ` · ${editorStatus.selection.length.toLocaleString()} selected (Run runs the selection)`
                : editorStatus ? ` · ${editorStatus.chars.toLocaleString()} chars` : ""}
            </span>
            <span>{editorNote ?? "⌘F find · ⌘/ comment · Ctrl+Space suggest · Shift+Alt+F format"}</span>
          </div>
          <p className="muted eq-wb-note" id="sql-note">
            {runsOn} Running here doesn&apos;t use the AI or any tokens.
            {target === "workspace" && (
              <>
                {" "}
                <button type="button" className="odn-linklike" onClick={() => setTarget("connection")}>
                  Run on the warehouse instead
                </button>
              </>
            )}
          </p>
        </section>

        {outcome?.kind === "confirm" && (
          <section role="alertdialog" aria-label="Confirm expensive query" className="confirm">
            <p>
              This query will scan about <strong>{formatBytes(outcome.bytes)}</strong>, which is above your
              workspace&apos;s confirmation threshold.
            </p>
            <button type="button" onClick={() => run(true, lastRunText ?? sql)}>Run anyway</button>{" "}
            <button type="button" onClick={() => setOutcome(null)}>Cancel</button>
          </section>
        )}

        {outcome?.kind === "error" && (
          <p role="alert" className="error">
            {outcome.message}
          </p>
        )}

        {outcome?.kind === "result" && (
          <section aria-label="Results">
            <p className="muted">
              {outcome.result.meta.rows.toLocaleString()} {outcome.result.meta.rows === 1 ? "row" : "rows"} ·{" "}
              {outcome.result.savedAs
                ? <>saved as <code>{outcome.result.savedAs}</code></>
                : <>scanned {formatBytes(outcome.result.meta.bytes_processed)}</>} ·{" "}
              {outcome.result.meta.duration_ms} ms
              {outcome.result.meta.truncated && <strong> · partial result (row limit reached)</strong>}
            </p>
            <div className="results">
              <table className="grid">
                <thead>
                  <tr>
                    {outcome.result.meta.columns.map((c) => (
                      <th key={c.name} scope="col" title={c.type}>
                        {c.name}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {outcome.result.rows.map((r, i) => (
                    <tr key={i}>
                      {outcome.result.meta.columns.map((c) => (
                        <td key={c.name}>{cell(r[c.name])}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}

        {schema && (
          <section className="sqled-dictionary" aria-label="Table dictionary">
            <DictionaryPanel
              connectionId={connectionId}
              path={schema.path}
              rowCount={schema.row_count}
              sizeLabel={formatBytes(schema.size_bytes)}
              canEdit={role === "data_lead" || role === "admin"}
              focusColumn={focusColumn}
            />
          </section>
        )}
      </>
  );

  const workbench = (
    <div
      ref={workbenchRef}
      className="odn-workbench"
      role="dialog"
      aria-label="SQL workbench"
      tabIndex={-1}
      onKeyDown={(e) => {
        // Esc inside the SQL editor belongs to the editor (close suggestions, search, selection), not the workbench.
        if (e.key === "Escape" && !(e.target as HTMLElement).closest(".cm-editor")) {
          e.stopPropagation();
          onWorkbenchOpenChange(false);
        }
      }}
    >
      <div className="odn-workbench-head">
        <div className="odn-workbench-title">
          <span>SQL workbench</span>
          {selectedPath && <code title={selectedPath}>{selectedPath}</code>}
        </div>
        <div className="odn-workbench-actions">
          <button
            type="button"
            className="odn-btn odn-btn--outline odn-btn--small"
            onClick={() => onWorkbenchOpenChange(false)}
          >
            Back to chat
          </button>
        </div>
      </div>
      <div className="workspace">{workbenchBody}</div>
    </div>
  );

  return (
    <>
      {sidebar}
      {connectionId && (
        <div className="odn-selection">
          {schema ? (
            <>
              <span className="odn-panel-heading">Selected {schema.kind}</span>
              <span className="odn-selection-path">{schema.path.join(".")}</span>
              <span className="odn-selection-meta">
                {schema.columns.length} columns
                {schema.row_count != null && ` · ${schema.row_count.toLocaleString()} rows`}
                {schema.size_bytes != null && ` · ${formatBytes(schema.size_bytes)}`} · chat is scoped to this table
              </span>
              <div className="odn-selection-actions">
                <button type="button" className="odn-btn odn-btn--on-panel odn-btn--small" onClick={() => onWorkbenchOpenChange(true)}>
                  Details &amp; SQL
                </button>
                <button type="button" className="odn-btn odn-btn--ghost-panel odn-btn--small" onClick={() => onScopeChange(null)}>
                  Clear selection
                </button>
              </div>
            </>
          ) : (
            <div className="odn-selection-actions">
              <button type="button" className="odn-btn odn-btn--ghost-panel odn-btn--small" onClick={() => onWorkbenchOpenChange(true)}>
                Open SQL workbench
              </button>
            </div>
          )}
        </div>
      )}
      {workbenchOpen && workbenchHost && createPortal(workbench, workbenchHost)}
    </>
  );
}
