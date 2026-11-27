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
  type DatasetHit,
  type MyTables,
  type SearchIndex,
} from "./CatalogSearch";
import { CONNECTIONS_CHANGED } from "@/lib/client-api";
import { connectionFor, isWorkspaceSql, tablesIn } from "@/lib/sqlRefs";

import DictionaryPanel from "./DictionaryPanel";
import { QueryCheckLine, useQueryCheck } from "./QueryCheck";
import WorkbenchResults from "./WorkbenchResults";
import { fieldPath, fillRate, profileField, ProfileStatus, useColumnProfile } from "./ColumnProfile";
import { DIALECT_LABEL, type EditorStatus, type SqlSchema } from "./SqlEditor";
import { dialectFor, formatSql } from "@/lib/sqlFormat";

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
const SEARCH_MODE_KEY = "odn.explorer.searchMode";
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
type TableSchema = {
  path: string[]; kind: "table" | "view"; columns: Column[]; row_count?: number | null; size_bytes?: number | null;
  description?: string | null; modified_at?: string | null; partitioning?: string | null; clustering?: string[] | null;
};
type QueryMeta = { rows: number; bytes_processed: number | null; duration_ms: number; truncated: boolean; columns: Column[]; job_id?: string | null };
type QueryResult = { meta: QueryMeta; tables: string[]; bytes_estimated: number | null; rows: Record<string, unknown>[] };
type MetadataStatus = {
  state: "not_indexed" | "building" | "ready" | "stale" | "partial" | "failed";
  last_success_at: string | null;
  job_status: string | null;
  error_code: string | null;
  progress_done: number | null;
  progress_total: number | null;
  finished_at: string | null;
  last_completed_status: string | null;
  last_completed_error_code: string | null;
  can_refresh: boolean;
};
type StoredTableHit = {
  path: string[];
  container_path: string[];
  container_name: string;
  name: string;
  kind: string;
  matching_fields: { path: string[]; data_type: string; description: string | null }[];
};
type StoredPage = { status: MetadataStatus; items: StoredTableHit[]; next_cursor: string | null };
type SearchMode = "stored" | "browser" | "shared";
type DownloadedIndex = { status: MetadataStatus; datasets: SearchIndex["datasets"] };

function ExplorerProfile({ connectionId, schema }: { connectionId: string; schema: TableSchema }) {
  const { profile, error, disabled } = useColumnProfile(connectionId, schema.path);
  if (disabled) return null;
  return (
    <details>
      <summary>Field fill rates</summary>
      <ProfileStatus profile={profile} error={error} />
      <ul>
        {schema.columns.map((column) => (
          <li key={column.name}>
            <code>{column.name}</code>: {fillRate(profileField(profile, column.name, column.type), profile)}
          </li>
        ))}
        {profile?.fields.filter((field) => field.path.length > 1).map((field) => (
          <li key={JSON.stringify(field.path)}>
            <code>{fieldPath(field)}</code>: {fillRate(field, profile)}
          </li>
        ))}
      </ul>
    </details>
  );
}

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

export default function Explorer({
  scope,
  onScopeChange,
  onConnectionChange,
  workbenchHost,
  workbenchOpen,
  onWorkbenchOpenChange,
  sqlRequest,
  sessionId,
  focus = false,
  onFocusChange,
  onAskAbout,
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
  /** Workbench "Focus": the side panel is hidden while the workbench is open. */
  focus?: boolean;
  onFocusChange?: (on: boolean) => void;
  /** Leave the workbench with a question about the result pre-filled in the chat (not sent). */
  onAskAbout?: (text: string) => void;
}) {
  const [connections, setConnections] = useState<Connection[]>([]);
  const [connectionId, setConnectionId] = useState<string>("");
  const [datasets, setDatasets] = useState<CatalogNode[]>([]);
  const [tables, setTables] = useState<Record<string, CatalogNode[]>>({});
  const [search, setSearch] = useState("");
  const deferredSearch = useDeferredValue(search);
  const [loadedIndex, setLoadedIndex] = useState<{
    connectionId: string; mode: "browser" | "shared"; generation: number; index: SearchIndex; duration: number;
  } | null>(null);
  const [indexState, setIndexState] = useState<"loading" | "ready" | "error">("loading");
  const [indexError, setIndexError] = useState<string | null>(null);
  const [indexAttempt, setIndexAttempt] = useState(0);
  const [searchMode, setSearchMode] = useState<SearchMode>("stored");
  const [metadataAvailability, setMetadataAvailability] = useState<"checking" | "available" | "unavailable" | "error">("checking");
  const [metadataStatus, setMetadataStatus] = useState<MetadataStatus | null>(null);
  const [metadataError, setMetadataError] = useState<string | null>(null);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [refreshingMetadata, setRefreshingMetadata] = useState(false);
  const [storedPage, setStoredPage] = useState<StoredPage | null>(null);
  const [storedPageKey, setStoredPageKey] = useState("");
  const [storedLoading, setStoredLoading] = useState(false);
  const [storedError, setStoredError] = useState<string | null>(null);
  const [storedDuration, setStoredDuration] = useState<number | null>(null);
  const [metadataGeneration, setMetadataGeneration] = useState(0);
  const [browserSearch, setBrowserSearch] = useState<{
    results: DatasetHit[]; duration: number; query: string; index: SearchIndex; prefs: ReturnType<typeof prefsOf>;
  } | null>(null);
  const searchSerial = useRef(0);
  const refreshSerial = useRef(0);
  const mineSerial = useRef(0);
  const [hideSnapshots, setHideSnapshots] = useState(true);
  const [mine, setMine] = useState<MyTables | null>(null);
  const [schema, setSchema] = useState<TableSchema | null>(null);
  const [focusColumn, setFocusColumn] = useState<string | null>(null);
  const [role, setRole] = useState<string | null>(null);
  const [sql, setSql] = useState("");
  const [editorStatus, setEditorStatus] = useState<EditorStatus | null>(null);
  // Wrap is on by default: the workbench is narrow and long `project.dataset.table` paths are common.
  const [wrap, setWrap] = useState(() => typeof window === "undefined" || localStorage.getItem(WRAP_KEY) !== "0");
  // Field-name clicks in the table pane insert at the editor cursor; the nonce makes repeats insert again.
  const [insertReq, setInsertReq] = useState<{ text: string; nonce: number } | null>(null);
  // Remounts the results panel per run so it opens on Results.
  const [runNonce, setRunNonce] = useState(0);
  // Side-by-side table pane only when the workbench is wide enough; otherwise the pane folds into a tab.
  const [wide, setWide] = useState(false);
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
    const savedMode = localStorage.getItem(SEARCH_MODE_KEY);
    if (savedMode === "browser" || savedMode === "shared") setSearchMode(savedMode);
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
    const serial = ++mineSerial.current;
    api<MyTables>(`connections/${connectionId}/my-tables`)
      .then((result) => { if (serial === mineSerial.current) setMine(result); })
      .catch(() => { if (serial === mineSerial.current) setMine(null); });
  }, [connectionId]);

  useEffect(() => {
    if (!connectionId) return;
    let cancelled = false;
    const mineRequests = mineSerial;
    setDatasets([]);
    setTables({});
    setSchema(null);
    onScopeChange(null);
    setMine(null);
    loadMine();
    api<CatalogNode[]>(`connections/${connectionId}/catalog`)
      .then((nodes) => { if (!cancelled) setDatasets(nodes); })
      .catch((e: Error) => { if (!cancelled) setError(e.message); });
    setLoadedIndex(null);
    setIndexState("loading");
    setIndexError(null);
    refreshSerial.current++;
    setRefreshingMetadata(false);
    setRefreshError(null);
    setMetadataAvailability("checking");
    setMetadataStatus(null);
    setMetadataError(null);
    return () => {
      cancelled = true;
      mineRequests.current++;
    };
  }, [connectionId, loadMine, onScopeChange]);

  useEffect(() => {
    if (!connectionId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    let previousTerminal: string | null = null;
    const poll = async () => {
      try {
        const status = await api<MetadataStatus>(`connections/${connectionId}/metadata-status`);
        if (cancelled) return;
        if (status.state !== "building") {
          const terminal = `${status.last_success_at}|${status.last_completed_status}|${status.finished_at}`;
          if (previousTerminal !== null && terminal !== previousTerminal) setMetadataGeneration((n) => n + 1);
          previousTerminal = terminal;
        }
        setMetadataStatus(status);
        setMetadataAvailability("available");
        setMetadataError(null);
      } catch (e) {
        if (cancelled) return;
        const err = e as ApiError;
        if (err.status === 404) {
          setMetadataAvailability("unavailable");
          return;
        }
        setMetadataAvailability("error");
        setMetadataError(err.message);
      }
      if (!cancelled) timer = setTimeout(poll, 5000);
    };
    void poll();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [connectionId]);

  const useStored = metadataAvailability === "available" && searchMode === "stored";
  const useShared = metadataAvailability === "available" && searchMode === "shared";
  const useSourceIndex = metadataAvailability === "unavailable" ||
    (metadataAvailability !== "checking" && searchMode === "browser");
  const useBrowser = useSourceIndex || useShared;
  const indexMode = useShared ? "shared" : "browser";
  const currentIndex = loadedIndex?.connectionId === connectionId && loadedIndex.mode === indexMode &&
    (indexMode === "browser" || loadedIndex.generation === metadataGeneration) ? loadedIndex : null;
  const index = currentIndex?.index ?? null;
  const indexDuration = currentIndex?.duration ?? null;

  useEffect(() => {
    if (!connectionId || !useBrowser || index || (useShared && metadataStatus?.state === "building")) return;
    let cancelled = false;
    const controller = new AbortController();
    const start = performance.now();
    const load = Promise.resolve().then(() => {
      if (cancelled) return null;
      setIndexState("loading");
      setIndexError(null);
      return useShared
        ? api<DownloadedIndex>(`connections/${connectionId}/metadata-index`, { signal: controller.signal }).then((snapshot): SearchIndex => ({
            status: snapshot.status.state === "ready" ? "success" : "partial",
            datasets: snapshot.datasets,
            failed: [],
          }))
        : Promise.all([
            api<SearchIndex>(`connections/${connectionId}/search-index`, { signal: controller.signal }),
            api<CuratedRow[]>(`connections/${connectionId}/definitions`, { signal: controller.signal }).catch(() => [] as CuratedRow[]),
          ]).then(([idx, defs]) => withCuratedDefinitions(idx, defs));
    });
    load
      .then((idx) => {
        if (cancelled || !idx) return;
        setLoadedIndex({ connectionId, mode: indexMode, generation: metadataGeneration, index: idx, duration: performance.now() - start });
        setIndexState("ready");
      })
      .catch((e: Error) => {
        if (!cancelled) {
          setIndexError(e.message);
          setIndexState("error");
        }
      });
    return () => { cancelled = true; controller.abort(); };
  }, [connectionId, useBrowser, useShared, indexMode, index, indexAttempt, metadataGeneration, metadataStatus?.state]);

  const changeSearchMode = (mode: SearchMode) => {
    localStorage.setItem(SEARCH_MODE_KEY, mode);
    setLoadedIndex(null);
    setIndexState("loading");
    setIndexError(null);
    setSearchMode(mode);
  };

  const refreshMetadata = async () => {
    const serial = refreshSerial.current;
    setRefreshingMetadata(true);
    setRefreshError(null);
    try {
      await api(`connections/${connectionId}/metadata-refresh`, { method: "POST" });
      const status = await api<MetadataStatus>(`connections/${connectionId}/metadata-status`);
      if (serial === refreshSerial.current) setMetadataStatus(status);
    } catch (e) {
      if (serial === refreshSerial.current) setRefreshError((e as Error).message);
    } finally {
      if (serial === refreshSerial.current) setRefreshingMetadata(false);
    }
  };

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
  useEffect(() => {
    if (!useBrowser || !visibleIndex || !terms.length) return;
    const timer = setTimeout(() => {
      const start = performance.now();
      const results = searchIndex(visibleIndex, terms, prefs);
      setBrowserSearch({ results, duration: performance.now() - start, query: deferredSearch, index: visibleIndex, prefs });
    }, 0);
    return () => clearTimeout(timer);
  }, [useBrowser, visibleIndex, terms, prefs, deferredSearch]);
  const currentBrowserSearch = browserSearch?.query === deferredSearch && browserSearch.index === visibleIndex &&
    browserSearch.prefs === prefs ? browserSearch : null;

  const storedKey = JSON.stringify([
    connectionId, deferredSearch, hideSnapshots, metadataGeneration, mine?.favorites, mine?.recent,
  ]);
  const loadStoredPage = useCallback(async (cursor: string | null, serial: number, signal?: AbortSignal) => {
    const params = new URLSearchParams({ q: deferredSearch.trim(), limit: "50", include_snapshots: String(!hideSnapshots) });
    if (cursor) params.set("cursor", cursor);
    const start = performance.now();
    try {
      const page = await api<StoredPage>(`connections/${connectionId}/metadata-search?${params}`, { signal });
      if (serial !== searchSerial.current) return;
      setStoredPage((previous) => cursor && previous ? { ...page, items: [...previous.items, ...page.items] } : page);
      setStoredPageKey(storedKey);
      setStoredDuration(performance.now() - start);
      setMetadataStatus(page.status);
      setStoredError(null);
    } catch (e) {
      if (serial === searchSerial.current && !(e instanceof DOMException && e.name === "AbortError")) {
        setStoredError((e as Error).message);
      }
    } finally {
      if (serial === searchSerial.current) setStoredLoading(false);
    }
  }, [connectionId, deferredSearch, hideSnapshots, storedKey]);

  useEffect(() => {
    const serial = ++searchSerial.current;
    const controller = new AbortController();
    let requestTimer: ReturnType<typeof setTimeout>;
    const timer = setTimeout(() => {
      setStoredPage(null);
      setStoredError(null);
      setStoredDuration(null);
      if (!useStored || !connectionId || !terms.length) {
        setStoredLoading(false);
        return;
      }
      setStoredLoading(true);
      requestTimer = setTimeout(() => { void loadStoredPage(null, serial, controller.signal); }, 250);
    }, 0);
    return () => { clearTimeout(timer); clearTimeout(requestTimer); controller.abort(); };
  }, [useStored, connectionId, terms, loadStoredPage, metadataGeneration]);

  const currentStoredPage = storedPageKey === storedKey ? storedPage : null;
  const storedResults = useMemo(() => {
    if (!currentStoredPage || !terms.length) return null;
    const groups = new Map<string, DatasetHit>();
    for (const hit of currentStoredPage.items) {
      const key = JSON.stringify(hit.container_path);
      let group = groups.get(key);
      if (!group) {
        group = { dataset: { path: hit.container_path, name: hit.container_name, tables: [] }, hits: [] };
        groups.set(key, group);
      }
      const columns = hit.matching_fields.map((f) => ({ name: f.path.join("."), type: f.data_type, description: f.description }));
      group.hits.push({ table: { path: hit.path, name: hit.name, kind: hit.kind, columns, columnCount: null }, columnHits: columns });
    }
    return [...groups.values()];
  }, [currentStoredPage, terms]);
  const results = useStored ? storedResults : currentBrowserSearch?.results ?? null;

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
  useEffect(() => {
    const el = workbenchRef.current;
    if (!workbenchOpen || !el) return;
    const observer = new ResizeObserver(([entry]) => setWide(entry.contentRect.width >= 1080));
    observer.observe(el);
    return () => observer.disconnect();
  }, [workbenchOpen, workbenchHost]);

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
          setRunNonce((n) => n + 1);
          return;
        }
        const result = await api<QueryResult>(`connections/${connectionId}/query`, {
          method: "POST",
          body: JSON.stringify({ sql: sqlText, confirmed, source: "explorer", max_rows: 1000 }),
        });
        setOutcome({ kind: "result", result });
        setRunNonce((n) => n + 1);
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
  const browserStatus = useShared && !index && metadataStatus?.state === "building"
    ? "Metadata refresh is running; download will start when it finishes."
    : results
    ? `${tableHitCount} ${tableHitCount === 1 ? "table" : "tables"} match` +
      (fieldHitCount ? `, ${fieldHitCount} matching ${fieldHitCount === 1 ? "field" : "fields"}` : "") +
      hiddenNote
    : indexState === "loading"
      ? "Indexing datasets, tables and fields…"
      : indexState === "error"
        ? "Search index is unavailable; browse the tree below."
        : index?.status === "partial"
          ? `${indexedTables} tables indexed; metadata may be incomplete or stale.${hiddenNote}`
          : `${indexedTables} tables searchable${hiddenNote}`;
  const status = useStored
    ? storedLoading && !currentStoredPage ? "Searching stored metadata…"
      : storedError ? "Stored search failed."
        : results ? `${tableHitCount} tables loaded${currentStoredPage?.next_cursor ? "; more available" : ""}`
          : metadataStatus?.state === "not_indexed" ? "No stored metadata yet."
            : metadataStatus?.state === "building"
              ? `Indexing metadata${metadataStatus.progress_total != null ? `: ${metadataStatus.progress_done ?? 0}/${metadataStatus.progress_total} datasets` : "…"}`
              : "Search persisted metadata."
    : metadataAvailability === "error" && !useBrowser
      ? "Stored search is unavailable; choose browser index to continue."
      : browserStatus;
  const lastFailure = metadataStatus?.state === "building"
    ? metadataStatus.last_completed_status
    : metadataStatus?.state;
  const failureCode = metadataStatus?.state === "building"
    ? metadataStatus.last_completed_error_code
    : metadataStatus?.error_code;
  const metadataWarning = ["stale", "partial", "failed", "cancelled"].includes(lastFailure ?? "")
    ? failureCode === "credential_error"
      ? "Metadata refresh could not use these credentials. Use encrypted connection credentials or an immutable secret version."
      : "Metadata may be incomplete or stale because the last refresh did not succeed."
    : null;
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
        {(metadataAvailability === "available" || metadataAvailability === "error") && (
          <label>
            Search method
            <select value={searchMode} onChange={(e) => changeSearchMode(e.target.value as SearchMode)}>
              <option value="stored">Stored metadata API</option>
              <option value="shared">Shared downloaded index</option>
              <option value="browser">Browser full index</option>
            </select>
          </label>
        )}
        {(useStored || useShared) && metadataStatus && (
          <div className="metadata-controls">
            <span className="muted">
              {metadataStatus.last_success_at
                ? `Last complete refresh: ${new Date(metadataStatus.last_success_at).toLocaleString()}`
                : "No complete refresh yet."}
            </span>
            {metadataStatus.can_refresh ? (
              <button type="button" className="link" disabled={refreshingMetadata} onClick={() => void refreshMetadata()}>
                {refreshingMetadata ? "Starting…" : metadataStatus.state === "building" ? "Retry if stalled" :
                  metadataStatus.state === "not_indexed" ? "Build index" : "Refresh index"}
              </button>
            ) : metadataStatus.state === "not_indexed" && (
              <span className="muted">An authorized administrator or operator must build this index.</span>
            )}
          </div>
        )}
        {(metadataWarning || (useShared && index?.status === "partial" ? "Downloaded metadata may be incomplete or stale." : null)) &&
          (useStored || useShared) && (
            <p role="alert" className="error">
              {metadataWarning ?? "Downloaded metadata may be incomplete or stale."}
            </p>
          )}
        {useShared && index && metadataStatus?.state === "building" && (
          <p className="muted">Refresh in progress; searching the previously downloaded index.</p>
        )}
        {(metadataError || refreshError) && <p role="alert" className="error">{refreshError ?? metadataError}</p>}
        {useBrowser && indexState === "error" && indexError && (
          <p role="alert" className="error">
            {indexError}{" "}
            <button type="button" className="link" onClick={() => setIndexAttempt((n) => n + 1)}>Retry</button>
          </p>
        )}
        <div className="search-row">
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => e.key === "Escape" && setSearch("")}
            placeholder="Search datasets, tables and fields"
            aria-label="Search datasets, tables and fields"
            disabled={useBrowser && indexState === "error"}
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
          {useStored && storedDuration != null && ` · API ${Math.round(storedDuration)} ms`}
          {useBrowser && currentBrowserSearch && ` · browser ${Math.round(currentBrowserSearch.duration)} ms`}
          {useBrowser && indexDuration != null && ` · ${useShared ? "shared download" : "index load"} ${(indexDuration / 1000).toFixed(2)} s`}
        </p>
        {useStored && terms.length > 0 && storedLoading && !currentStoredPage ? (
          <p className="muted">Searching…</p>
        ) : useStored && terms.length > 0 && storedError ? (
          <p role="alert" className="error">{storedError}</p>
        ) : results ? (
          results.length ? (
            <>
            <CatalogSearchResults
              results={results}
              terms={terms}
              selectedPath={selectedPath}
              onOpen={openTable}
              prefs={prefs}
              onToggleFavorite={toggleFavorite}
            />
            {useStored && currentStoredPage?.next_cursor && currentStoredPage.items.length < 200 && (
              <button
                type="button"
                className="link"
                disabled={storedLoading}
                onClick={() => {
                  setStoredLoading(true);
                  void loadStoredPage(currentStoredPage.next_cursor, searchSerial.current);
                }}
              >
                {storedLoading ? "Loading…" : "Load more"}
              </button>
            )}
            {useStored && currentStoredPage && currentStoredPage.items.length >= 200 && currentStoredPage.next_cursor && (
              <p className="muted">Showing the first 200 tables; refine your search.</p>
            )}
            </>
          ) : (
            <p className="muted">
              {useStored && metadataStatus?.state === "not_indexed"
                ? "Build the metadata index before searching."
                : useStored && metadataStatus?.state === "building"
                  ? "No matches indexed yet; refresh is still running."
                  : `No datasets, tables or fields match “${deferredSearch.trim()}”.`}
            </p>
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
  // What Run would send: the selection if any, else the whole editor.
  const runnable = editorStatus?.selection.trim() ? editorStatus.selection : sql;
  const live = useQueryCheck(target === "connection" && connectionId ? connectionId : null, runnable);

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

  const dictionary = schema && (
    <DictionaryPanel
      key={selectedPath}
      connectionId={connectionId}
      path={schema.path}
      kind={schema.kind}
      rowCount={schema.row_count}
      sizeLabel={schema.size_bytes ? formatBytes(schema.size_bytes) : null}
      modifiedAt={schema.modified_at}
      partitioning={schema.partitioning}
      clustering={schema.clustering}
      canEdit={role === "data_lead" || role === "admin"}
      focusColumn={focusColumn}
      initialFilter={search.trim()}
      onInsertField={(name) => setInsertReq({ text: name, nonce: Date.now() })}
      compact={wide}
    />
  );
  const sidePane = wide && !!dictionary;
  const result = outcome?.kind === "result" ? outcome.result : null;
  const resultsPanel = (result || (dictionary && !sidePane)) && (
    <WorkbenchResults
      key={runNonce}
      result={result}
      ranSql={lastRunText}
      connectionName={activeConnectionName}
      fileName={(schema?.path[schema.path.length - 1] ?? "query").replace(/[^\w.-]+/g, "_")}
      fields={sidePane ? undefined : dictionary || undefined}
      fieldsLabel={schema ? `Fields (${schema.columns.length})` : "Fields"}
      onAskAbout={onAskAbout}
    />
  );

  const workbenchBody = (
    <div className={`wb-layout${sidePane ? " wb-layout--wide" : ""}`}>
      <div className="wb-main">
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
              {onFocusChange && (
                <button
                  type="button"
                  className="sqled-btn"
                  aria-pressed={focus}
                  onClick={() => onFocusChange(!focus)}
                  title={focus ? "Show the side panel again" : "Hide the side panel while the workbench is open (remembered)"}
                >
                  ⤢ Focus
                </button>
              )}
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
          <div className="sqled-frame">
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
              insert={insertReq}
            />
          </div>
          <QueryCheckLine
            check={live.check}
            loading={live.loading}
            failed={live.failed}
            fallback={target === "workspace" ? "Runs on this conversation's results: no warehouse scan." : undefined}
          />
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

        {resultsPanel}
      </div>
      {sidePane && <aside className="wb-side" aria-label="Table">{dictionary}</aside>}
    </div>
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
              <ExplorerProfile connectionId={connectionId} schema={schema} />
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
