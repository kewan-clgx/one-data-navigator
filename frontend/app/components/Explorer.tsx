"use client";

import { useCallback, useDeferredValue, useEffect, useMemo, useState } from "react";

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

import DictionaryPanel from "./DictionaryPanel";
import SplitPane from "./SplitPane";

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
const MY_TABLES_SHOWN = 8;
const PREVIEW_ROWS = 20;
// BigQuery checks the bytes-billed cap against the UNSAMPLED size of the columns read, so avoid SELECT *.
const PREVIEW_COLUMNS = 10;
// Below this size a full read is cheap, and block sampling could return no rows at all.
const PREVIEW_SAMPLE_MIN_BYTES = 1024 ** 3;

function previewSql(s: TableSchema, focus: string | null): string {
  const names = s.columns.map((c) => c.name).filter((n) => !n.includes("."));
  const cols = (focus && names.includes(focus) ? [focus, ...names.filter((n) => n !== focus)] : names).slice(
    0,
    PREVIEW_COLUMNS,
  );
  const sample = (s.size_bytes ?? Infinity) > PREVIEW_SAMPLE_MIN_BYTES ? " TABLESAMPLE SYSTEM (1 PERCENT)" : "";
  return `SELECT ${cols.join(", ")}\nFROM \`${s.path.join(".")}\`${sample}\nLIMIT ${PREVIEW_ROWS}`;
}

type Connection = { id: string; name: string; kind: string; scope?: "shared" | "personal"; allowed_objects: string[] };
type CatalogNode = { kind: string; path: string[]; name: string };
type Column = { name: string; type: string; nullable: boolean; description?: string | null };
type TableSchema = { path: string[]; kind: "table" | "view"; columns: Column[]; row_count?: number | null; size_bytes?: number | null; description?: string | null };
type QueryMeta = { rows: number; bytes_processed: number | null; duration_ms: number; truncated: boolean; columns: Column[] };
type QueryResult = { meta: QueryMeta; tables: string[]; bytes_estimated: number | null; rows: Record<string, unknown>[] };

type Outcome =
  | { kind: "result"; result: QueryResult }
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

export default function Explorer() {
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
        // Keep the current selection if it still exists (e.g. after adding a connection in Settings).
        setConnectionId((id) => (cs.some((c) => c.id === id) ? id : cs[0]?.id ?? ""));
      })
      .catch((e: Error) => setError(e.message));
  }, []);

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
  }, [connectionId, loadMine]);

  const prefs = useMemo(() => prefsOf(mine), [mine]);

  // Stored paths are lowercase (matching is case-insensitive); map back to the catalog's spelling.
  const realPaths = useMemo(() => {
    const m = new Map<string, string[]>();
    datasets.forEach((d) => m.set(pathKey(d.path), d.path));
    index?.datasets.forEach((d) => {
      m.set(pathKey(d.path), d.path);
      d.tables.forEach((t) => m.set(pathKey(t.path), t.path));
    });
    return m;
  }, [datasets, index]);
  const resolvePath = useCallback((key: string) => realPaths.get(key) ?? key.split("."), [realPaths]);

  const toggleFavorite = useCallback(
    async (path: string[]) => {
      const key = pathKey(path);
      const on = prefs.favorites.has(key);
      setMine((m) =>
        m && {
          ...m,
          favorites: on
            ? m.favorites.filter((f) => f.path !== key)
            : [{ path: key, kind: path.length >= 3 ? "table" : "dataset" }, ...m.favorites],
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
    [connectionId, prefs, loadMine],
  );

  const datasetRank = useCallback(
    (ds: CatalogNode) => {
      const key = pathKey(ds.path);
      const inside = (k: string) => k.startsWith(`${key}.`);
      if (prefs.favorites.has(key) || [...prefs.favorites].some(inside)) return 2;
      return [...prefs.recent.keys()].some(inside) ? 1 : 0;
    },
    [prefs],
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
    async (path: string[], column?: string) => {
      try {
        const s = await api<TableSchema>(`connections/${connectionId}/schema?${query({ path })}`);
        setSchema(s);
        setFocusColumn(column ?? null);
        const names = s.columns.map((c) => c.name).filter((n) => !n.includes("."));
        const picked = column && !column.includes(".") ? [column, ...names.filter((n) => n !== column)] : names;
        setSql(`SELECT ${picked.slice(0, 5).join(", ")}\nFROM \`${path.join(".")}\`\nLIMIT 100`);
        setOutcome(null);
      } catch (e) {
        setError((e as Error).message);
      }
    },
    [connectionId],
  );

  const run = useCallback(
    async (confirmed: boolean, sqlText: string = sql) => {
      setRunning(true);
      setOutcome(null);
      try {
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
        } else if (typeof detail === "object" && (detail.rejected || detail.failed)) {
          setOutcome({ kind: "error", message: String(detail.rejected ?? detail.failed) });
        } else {
          setOutcome({ kind: "error", message: err.message });
        }
      } finally {
        setRunning(false);
      }
    },
    [connectionId, sql, loadMine],
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

  return (
    <SplitPane
      className="explorer"
      storageKey="odn.split.explorer"
      defaultFraction={0.3}
      minLeft={280}
      minRight={360}
      label="Resize explorer panel"
      left={
      <aside className="sidebar" aria-label="Schema explorer">
        <label>
          Connection
          <select value={connectionId} onChange={(e) => setConnectionId(e.target.value)}>
            {connections.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name} ({c.kind}{c.scope === "personal" ? ", yours" : ""})
              </option>
            ))}
          </select>
        </label>
        {connections.length === 0 && !error && (
          <p className="muted">No data connections yet. Add your own BigQuery connection in the Settings tab.</p>
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
      }
      right={
      <main className="workspace">
        {error && (
          <p role="alert" className="error">
            {error} <button type="button" onClick={() => setError(null)}>Dismiss</button>
          </p>
        )}

        {schema && (
          <DictionaryPanel
            connectionId={connectionId}
            path={schema.path}
            rowCount={schema.row_count}
            sizeLabel={formatBytes(schema.size_bytes)}
            canEdit={role === "data_lead" || role === "admin"}
            focusColumn={focusColumn}
          />
        )}

        <section aria-label="SQL">
          <label htmlFor="sql">SQL (read-only, checked against this connection&apos;s allowlist)</label>
          <textarea id="sql" value={sql} onChange={(e) => setSql(e.target.value)} rows={8} spellCheck={false} />
          <button type="button" onClick={() => run(false)} disabled={!sql.trim() || running || !connectionId}>
            {running ? "Running…" : "Run"}
          </button>{" "}
          {schema && (
            <button
              type="button"
              onClick={() => {
                const text = previewSql(schema, focusColumn);
                setSql(text);
                run(false, text);
              }}
              disabled={running || !connectionId || schema.kind === "view"}
              title={
                schema.kind === "view"
                  ? "Sampling isn't available for views; write a filtered query instead"
                  : `Run ${PREVIEW_ROWS} rows of the first ${PREVIEW_COLUMNS} columns from a ~1% block sample`
              }
            >
              Preview data
            </button>
          )}
        </section>

        {outcome?.kind === "confirm" && (
          <section role="alertdialog" aria-label="Confirm expensive query" className="confirm">
            <p>
              This query will scan about <strong>{formatBytes(outcome.bytes)}</strong>, which is above your
              workspace&apos;s confirmation threshold.
            </p>
            <button type="button" onClick={() => run(true)}>Run anyway</button>{" "}
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
              {outcome.result.meta.rows.toLocaleString()} {outcome.result.meta.rows === 1 ? "row" : "rows"} · scanned{" "}
              {formatBytes(outcome.result.meta.bytes_processed)} ·{" "}
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
      </main>
      }
    />
  );
}
