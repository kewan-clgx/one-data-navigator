"use client";

import { Fragment, useState } from "react";

export type IndexedColumn = { name: string; type: string; description?: string | null };
export type IndexedTable = { path: string[]; name: string; kind: string; columns: IndexedColumn[] };
export type IndexedDataset = { path: string[]; name: string; tables: IndexedTable[] };
export type SearchIndex = { status: "success" | "partial"; datasets: IndexedDataset[]; failed: string[] };
export type MyTables = {
  favorites: { path: string; kind: "dataset" | "table" }[];
  recent: { path: string; query_count: number; last_used_at: string }[];
};
/** Lowercase dotted paths: favorites, and recently queried tables -> recency position (0 = latest). */
export type Prefs = { favorites: Set<string>; recent: Map<string, number>; caseSensitive: boolean };

export const NO_PREFS: Prefs = { favorites: new Set(), recent: new Map(), caseSensitive: false };

export function prefsOf(mine: MyTables | null, caseSensitive = false): Prefs {
  if (!mine) return { ...NO_PREFS, caseSensitive };
  return {
    favorites: new Set(mine.favorites.map((f) => f.path)),
    recent: new Map(mine.recent.map((r, i) => [r.path, i])),
    caseSensitive,
  };
}

export const pathKey = (path: string[], caseSensitive = false) =>
  caseSensitive ? path.join(".") : path.join(".").toLowerCase();

/** Favorite tables, then recently queried (latest first), then tables in a favorite dataset. */
export function tablePriority(prefs: Prefs, path: string[]): number {
  const key = pathKey(path, prefs.caseSensitive);
  if (prefs.favorites.has(key)) return 3000;
  const r = prefs.recent.get(key);
  if (r !== undefined) return 2000 - r;
  return prefs.favorites.has(pathKey(path.slice(0, -1), prefs.caseSensitive)) ? 1000 : 0;
}

export function datasetPriority(prefs: Prefs, path: string[], tablePaths: string[][]): number {
  const best = tablePaths.reduce((m, p) => Math.max(m, tablePriority(prefs, p)), 0);
  return Math.max(best, prefs.favorites.has(pathKey(path, prefs.caseSensitive)) ? 3000 : 0);
}

export function StarButton({
  path,
  label,
  prefs,
  onToggle,
}: {
  path: string[];
  label: string;
  prefs: Prefs;
  onToggle: (path: string[]) => void;
}) {
  const on = prefs.favorites.has(pathKey(path, prefs.caseSensitive));
  return (
    <button
      type="button"
      className="star"
      aria-pressed={on}
      aria-label={on ? `Remove ${label} from favorites` : `Add ${label} to favorites`}
      title={on ? "Remove from favorites" : "Add to favorites"}
      onClick={() => onToggle(path)}
    >
      {on ? "★" : "☆"}
    </button>
  );
}

const MAX_COLUMN_HITS = 6;
const MAX_TABLE_HITS = 200;

// Timestamped snapshot copies, e.g. foo_202610222329, foo_20261022, foo_20261022_bak (YYYYMMDD[HH[MM[SS]]]).
const SNAPSHOT_RE = /(?:^|_)(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])(?:\d{2}){0,3}(?:_|$)/;

export function isSnapshot(tableName: string): boolean {
  return SNAPSHOT_RE.test(tableName);
}

export function withoutSnapshots(index: SearchIndex): SearchIndex {
  return {
    ...index,
    datasets: index.datasets.map((d) => ({ ...d, tables: d.tables.filter((t) => !isSnapshot(t.name)) })),
  };
}

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export function Highlight({ text, terms }: { text: string; terms: string[] }) {
  if (!terms.length) return <>{text}</>;
  const pattern = new RegExp(`(${terms.map(escapeRegExp).join("|")})`, "gi");
  return (
    <>
      {text.split(pattern).map((part, i) =>
        i % 2 === 1 ? <mark key={i}>{part}</mark> : <Fragment key={i}>{part}</Fragment>,
      )}
    </>
  );
}

type TableHit = { table: IndexedTable; columnHits: IndexedColumn[] };
type DatasetHit = { dataset: IndexedDataset; hits: TableHit[] };

export function searchIndex(index: SearchIndex, terms: string[], prefs: Prefs = NO_PREFS): DatasetHit[] {
  const count = (ts: string[], ...texts: (string | null | undefined)[]) => {
    const text = texts.filter(Boolean).join(" ").toLowerCase();
    return ts.reduce((n, t) => n + (text.includes(t) ? 1 : 0), 0);
  };
  return index.datasets
    .map((dataset) => ({
      dataset,
      hits: dataset.tables
        .flatMap((table) => {
          const names = `${dataset.name} ${table.name}`.toLowerCase();
          const inColumns = (t: string) =>
            table.columns.some(
              (c) => c.name.toLowerCase().includes(t) || (c.description ?? "").toLowerCase().includes(t),
            );
          // Every term must match somewhere in the dataset/table/column hierarchy.
          if (!terms.every((t) => names.includes(t) || inColumns(t))) return [];
          // Terms the table name already explains matter less when ranking its fields.
          const fieldTerms = terms.filter((t) => !names.includes(t));
          const columnHits = table.columns
            .map((c) => ({
              c,
              s: 6 * count(fieldTerms, c.name) + 2 * count(fieldTerms, c.description) + count(terms, c.name),
            }))
            .filter((x) => x.s > 0)
            .sort((a, b) => b.s - a.s)
            .map((x) => x.c);
          return [{ table, columnHits, rank: tablePriority(prefs, table.path) + count(terms, table.name) }];
        })
        .sort((a, b) => b.rank - a.rank)
        .map(({ table, columnHits }) => ({ table, columnHits })),
    }))
    .filter((d) => d.hits.length > 0)
    .map((d) => ({ d, p: datasetPriority(prefs, d.dataset.path, d.hits.map((h) => h.table.path)) }))
    .sort((a, b) => b.p - a.p)
    .map(({ d }) => d);
}

export default function CatalogSearchResults({
  results,
  terms,
  selectedPath,
  onOpen,
  prefs,
  onToggleFavorite,
}: {
  results: DatasetHit[];
  terms: string[];
  selectedPath: string | null;
  onOpen: (path: string[], column?: string) => void;
  prefs: Prefs;
  onToggleFavorite: (path: string[]) => void;
}) {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [expandedCols, setExpandedCols] = useState<Set<string>>(new Set());
  const toggle = (set: Set<string>, key: string) => {
    const next = new Set(set);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    return next;
  };

  let shown = 0;

  return (
    <ul className="tree search-results" aria-label="Search results">
      {results.map(({ dataset, hits }) => {
        const dsKey = dataset.path.join(".");
        const open = !collapsed.has(dsKey);
        const columnCount = hits.reduce((n, h) => n + h.columnHits.length, 0);
        return (
          <li key={dsKey}>
            <div className="row">
            <button
              type="button"
              className="node dataset"
              aria-expanded={open}
              onClick={() => setCollapsed((s) => toggle(s, dsKey))}
            >
              <span aria-hidden>{open ? "▾" : "▸"}</span>
              <span className="label">
                <Highlight text={dataset.name} terms={terms} />
              </span>
              <span className="count">
                {hits.length} {hits.length === 1 ? "table" : "tables"}
                {columnCount > 0 && ` · ${columnCount} ${columnCount === 1 ? "field" : "fields"}`}
              </span>
            </button>
            <StarButton path={dataset.path} label={dataset.name} prefs={prefs} onToggle={onToggleFavorite} />
            </div>
            {open && (
              <ul>
                {hits.map(({ table, columnHits }) => {
                  if (shown >= MAX_TABLE_HITS) return null;
                  shown += 1;
                  const tKey = table.path.join(".");
                  const showAll = expandedCols.has(tKey);
                  const cols = showAll ? columnHits : columnHits.slice(0, MAX_COLUMN_HITS);
                  return (
                    <li key={tKey}>
                      <div className="row">
                      <button
                        type="button"
                        className={`node table ${selectedPath === tKey ? "selected" : ""}`}
                        aria-pressed={selectedPath === tKey}
                        title={tKey}
                        onClick={() => onOpen(table.path)}
                      >
                        <span aria-hidden>{table.kind === "view" ? "◇" : "▦"}</span>
                        <span className="label">
                          <Highlight text={table.name} terms={terms} />
                        </span>
                        {prefs.recent.has(pathKey(table.path, prefs.caseSensitive)) && <span className="badge">recent</span>}
                        <span className="count">{table.columns.length} cols</span>
                      </button>
                      <StarButton path={table.path} label={table.name} prefs={prefs} onToggle={onToggleFavorite} />
                      </div>
                      {columnHits.length > 0 && (
                        <ul className="fields" aria-label={`Matching fields in ${table.name}`}>
                          {cols.map((c) => (
                            <li key={c.name}>
                              <button
                                type="button"
                                className="node field"
                                title={c.description ?? c.name}
                                onClick={() => onOpen(table.path, c.name)}
                              >
                                <code className="label">
                                  <Highlight text={c.name} terms={terms} />
                                </code>
                                <span className="type">{c.type}</span>
                              </button>
                              {c.description && terms.some((t) => c.description!.toLowerCase().includes(t)) && (
                                <p className="field-desc">
                                  <Highlight text={c.description} terms={terms} />
                                </p>
                              )}
                            </li>
                          ))}
                          {columnHits.length > MAX_COLUMN_HITS && (
                            <li>
                              <button
                                type="button"
                                className="more"
                                onClick={() => setExpandedCols((s) => toggle(s, tKey))}
                              >
                                {showAll ? "Show fewer" : `+${columnHits.length - MAX_COLUMN_HITS} more`}
                              </button>
                            </li>
                          )}
                        </ul>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </li>
        );
      })}
      {results.reduce((n, d) => n + d.hits.length, 0) > MAX_TABLE_HITS && (
        <li className="muted">Showing the first {MAX_TABLE_HITS} tables; refine your search to narrow results.</li>
      )}
    </ul>
  );
}
