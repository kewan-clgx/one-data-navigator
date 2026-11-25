"use client";

import { useEffect, useState } from "react";

const PAGE = 100;

type Page = { total: number; rows: Record<string, unknown>[] };

function cell(v: unknown): string {
  if (v === null || v === undefined) return "NULL";
  return typeof v === "object" ? JSON.stringify(v) : String(v);
}

/** Server-paged, sortable grid over a workspace artifact (rows are read from Parquet by the worker). */
export default function ResultsGrid({
  sessionId,
  name,
  columns,
  initialRows,
  rowsBase,
}: {
  sessionId: string;
  name: string;
  columns: string[];
  initialRows: Record<string, unknown>[];
  /** Alternative source (e.g. a shared conversation's copy); its `/rows` is used and CSV download is hidden. */
  rowsBase?: string;
}) {
  const [offset, setOffset] = useState(0);
  const [sort, setSort] = useState<{ col: string; desc: boolean } | null>(null);
  const [page, setPage] = useState<Page | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const base = rowsBase ?? `/api/odn/workspace/${encodeURIComponent(sessionId)}/artifacts/${encodeURIComponent(name)}`;

  useEffect(() => {
    setOffset(0);
    setSort(null);
    setPage(null);
  }, [sessionId, name]);

  useEffect(() => {
    let cancelled = false;
    const q = new URLSearchParams({ offset: String(offset), limit: String(PAGE) });
    if (sort) {
      q.set("order_by", sort.col);
      q.set("descending", String(sort.desc));
    }
    setLoading(true);
    setError(null);
    fetch(`${base}/rows?${q}`)
      .then(async (r) => {
        const body = await r.json();
        if (!r.ok) throw new Error(typeof body.detail === "string" ? body.detail : `HTTP ${r.status}`);
        if (!cancelled) setPage(body);
      })
      .catch((e: Error) => !cancelled && setError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [base, offset, sort]);

  const total = page?.total;
  const rows = total === 0 ? [] : page?.rows ?? initialRows.slice(0, PAGE);
  const toggleSort = (col: string) => {
    setOffset(0);
    setSort((s) => (s?.col === col ? (s.desc ? null : { col, desc: true }) : { col, desc: false }));
  };

  return (
    <div className="results-grid">
      <div className="grid-toolbar">
        <span className="muted" role="status">
          {total === undefined
            ? loading ? "Loading…" : ""
            : total === 0
              ? "No rows"
              : total <= PAGE
                ? "" // the row count is already shown above the grid
                : `Rows ${offset + 1}–${Math.min(offset + PAGE, total)} of ${total.toLocaleString()}`}
        </span>
        <span className="pager">
          {/* Row pages only exist past PAGE rows; disabled buttons on a one-page result look broken. */}
          {total !== undefined && total > PAGE && (
            <>
              <button type="button" onClick={() => setOffset((o) => Math.max(0, o - PAGE))} disabled={offset === 0 || loading}>
                ‹ Prev {PAGE}
              </button>
              <button type="button" onClick={() => setOffset((o) => o + PAGE)} disabled={offset + PAGE >= total || loading}>
                Next {PAGE} ›
              </button>
            </>
          )}
          {!rowsBase && (
            <a className="download" href={`${base}/csv`} download={`${name}.csv`}>
              Download CSV
            </a>
          )}
        </span>
      </div>
      {error && <p role="alert" className="error">{error}</p>}
      <div className="results" aria-busy={loading}>
        <table className="grid">
          <thead>
            <tr>
              {columns.map((c) => (
                <th
                  key={c}
                  scope="col"
                  aria-sort={sort?.col === c ? (sort.desc ? "descending" : "ascending") : "none"}
                >
                  <button type="button" className="sort" onClick={() => toggleSort(c)}>
                    {c} <span aria-hidden>{sort?.col === c ? (sort.desc ? "▼" : "▲") : "↕"}</span>
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={offset + i}>
                {columns.map((c) => (
                  <td key={c}>{cell(r[c])}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
