"use client";

import { useState, type ReactNode } from "react";

import HScroll from "./HScroll";

type Column = { name: string; type: string };
export type WorkbenchResult = {
  meta: { rows: number; bytes_processed: number | null; duration_ms: number; truncated: boolean; columns: Column[]; job_id?: string | null };
  tables: string[];
  bytes_estimated: number | null;
  rows: Record<string, unknown>[];
  savedAs?: string;
};

type Tab = "results" | "details" | "fields";
const PAGE_SIZES = [25, 50, 100] as const;
const PAGE_SIZE_KEY = "odn.workbench.pageSize";
const NUMERIC = /^(u?int|float|double|decimal|numeric|bignumeric|number|real|bigint|smallint|tinyint)/i;

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

function text(v: unknown): string {
  return typeof v === "object" && v !== null ? JSON.stringify(v) : String(v);
}

function Cell({ value }: { value: unknown }) {
  if (value === null || value === undefined) return <span className="grid-null">null</span>;
  const t = text(value);
  return <span title={t.length > 60 ? t : undefined}>{t}</span>;
}

function csv(result: WorkbenchResult): string {
  const esc = (v: unknown) => (v === null || v === undefined ? "" : `"${text(v).replace(/"/g, '""')}"`);
  const cols = result.meta.columns.map((c) => c.name);
  return [cols.map(esc).join(","), ...result.rows.map((r) => cols.map((c) => esc(r[c])).join(","))].join("\n");
}

function tsv(result: WorkbenchResult): string {
  const cols = result.meta.columns.map((c) => c.name);
  const clean = (v: unknown) => (v === null || v === undefined ? "" : text(v).replace(/[\t\n]/g, " "));
  return [cols.join("\t"), ...result.rows.map((r) => cols.map((c) => clean(r[c])).join("\t"))].join("\n");
}

/**
 * The workbench's results panel: header with run summary, tabs (Results · Run details · Fields when the table pane is
 * folded in) and actions. The grid numbers rows, stripes them, mutes nulls and right-aligns numbers, BigQuery-style.
 */
export default function WorkbenchResults({
  result,
  ranSql,
  connectionName,
  fileName,
  fields,
  fieldsLabel = "Fields",
  onAskAbout,
}: {
  result: WorkbenchResult | null;
  ranSql: string | null;
  connectionName: string;
  fileName: string;
  /** The table pane, shown as a tab when the workbench is too narrow for a side pane. */
  fields?: ReactNode;
  fieldsLabel?: string;
  onAskAbout?: (text: string) => void;
}) {
  const [tab, setTab] = useState<Tab>(result ? "results" : "fields");
  const [pageSize, setPageSize] = useState<number>(() => {
    const saved = Number(typeof window === "undefined" ? 0 : localStorage.getItem(PAGE_SIZE_KEY));
    return (PAGE_SIZES as readonly number[]).includes(saved) ? saved : 50;
  });
  const [page, setPage] = useState(0);
  const [note, setNote] = useState<string | null>(null);

  const tabs: { id: Tab; label: string }[] = [
    ...(result ? [{ id: "results" as const, label: "Results" }, { id: "details" as const, label: "Run details" }] : []),
    ...(fields ? [{ id: "fields" as const, label: fieldsLabel }] : []),
  ];
  if (!tabs.length) return null;
  const active = tabs.some((t) => t.id === tab) ? tab : tabs[0].id;

  const total = result?.rows.length ?? 0;
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const current = Math.min(page, pages - 1);
  const start = current * pageSize;
  const visible = result?.rows.slice(start, start + pageSize) ?? [];

  const flash = (t: string) => {
    setNote(t);
    window.setTimeout(() => setNote(null), 2000);
  };
  const download = () => {
    if (!result) return;
    const url = URL.createObjectURL(new Blob([csv(result)], { type: "text/csv" }));
    const a = Object.assign(document.createElement("a"), { href: url, download: `${fileName}.csv` });
    a.click();
    URL.revokeObjectURL(url);
  };
  const askText = () => {
    if (!result) return "";
    if (result.savedAs) return `About result ${result.savedAs}: `;
    const n = result.meta.rows;
    return `About this query (${n.toLocaleString()} ${n === 1 ? "row" : "rows"} on ${connectionName}):\n\n\`\`\`sql\n${(ranSql ?? "").trim()}\n\`\`\`\n\n`;
  };

  return (
    <section className="wb-results" aria-label="Query results">
      <div className="wb-results-head">
        <div className="wb-results-title">
          <h3>{result ? "Query results" : fieldsLabel}</h3>
          {result && (
            <span className="muted">
              {result.meta.rows.toLocaleString()} {result.meta.rows === 1 ? "row" : "rows"} ·{" "}
              {result.savedAs
                ? <>saved as <code>{result.savedAs}</code></>
                : result.meta.bytes_processed === 0
                  ? (result.meta.job_id ? "cached, 0 B scanned" : "free preview, 0 B scanned")
                  : <>scanned {formatBytes(result.meta.bytes_processed)}</>} ·{" "}
              {result.meta.duration_ms.toLocaleString()} ms
              {result.meta.truncated && <strong> · partial (row limit reached)</strong>}
            </span>
          )}
        </div>
        {result && (
          <div className="wb-results-actions">
            {note && <span className="muted" role="status">{note}</span>}
            <button type="button" className="sqled-btn" onClick={download} title="Download these rows as CSV">CSV</button>
            <button
              type="button"
              className="sqled-btn"
              onClick={() => void navigator.clipboard?.writeText(tsv(result)).then(() => flash("Copied"))}
              title="Copy rows (tab-separated, pastes into spreadsheets)"
            >
              Copy
            </button>
            {onAskAbout && (
              <button
                type="button"
                className="odn-btn odn-btn--outline odn-btn--small"
                onClick={() => onAskAbout(askText())}
                title="Back to the chat with a question about this result pre-filled. Nothing is sent until you press Send."
              >
                Ask EasyQuery ↗
              </button>
            )}
          </div>
        )}
      </div>
      {tabs.length > 1 && (
        <div className="wb-tabs" role="tablist" aria-label="Results views">
          {tabs.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              id={`wb-tab-${t.id}`}
              aria-controls={`wb-panel-${t.id}`}
              aria-selected={active === t.id}
              className="wb-tab"
              onClick={() => setTab(t.id)}
            >
              {t.label}
            </button>
          ))}
        </div>
      )}

      {active === "results" && result && (
        <div role="tabpanel" id="wb-panel-results" aria-labelledby="wb-tab-results">
          {total === 0 ? (
            <p className="muted wb-empty">The query ran and returned no rows.</p>
          ) : (
            <>
              <HScroll className="results wb-grid-wrap" columns={result.meta.columns.length}>
                <table className="grid wb-grid">
                  <thead>
                    <tr>
                      <th scope="col" className="wb-rownum"><span className="sr-only">Row</span></th>
                      {result.meta.columns.map((c) => (
                        <th key={c.name} scope="col" title={c.type} className={NUMERIC.test(c.type) ? "num" : undefined}>
                          {c.name}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {visible.map((r, i) => (
                      <tr key={start + i}>
                        <th scope="row" className="wb-rownum">{start + i + 1}</th>
                        {result.meta.columns.map((c) => (
                          <td key={c.name} className={NUMERIC.test(c.type) ? "num" : undefined}>
                            <Cell value={r[c.name]} />
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </HScroll>
              <div className="wb-pager">
                <label>
                  Rows per page{" "}
                  <select
                    value={pageSize}
                    onChange={(e) => {
                      const n = Number(e.target.value);
                      localStorage.setItem(PAGE_SIZE_KEY, String(n));
                      setPageSize(n);
                      setPage(0);
                    }}
                  >
                    {PAGE_SIZES.map((n) => <option key={n} value={n}>{n}</option>)}
                  </select>
                </label>
                <span>{`${start + 1}–${Math.min(start + pageSize, total)} of ${total.toLocaleString()}`}</span>
                {pages > 1 && (
                  <span className="wb-pager-nav">
                    <button type="button" className="hscroll-btn" onClick={() => setPage(0)} disabled={current === 0} aria-label="First page">⏮</button>
                    <button type="button" className="hscroll-btn" onClick={() => setPage(current - 1)} disabled={current === 0} aria-label="Previous page">‹</button>
                    <button type="button" className="hscroll-btn" onClick={() => setPage(current + 1)} disabled={current >= pages - 1} aria-label="Next page">›</button>
                    <button type="button" className="hscroll-btn" onClick={() => setPage(pages - 1)} disabled={current >= pages - 1} aria-label="Last page">⏭</button>
                  </span>
                )}
              </div>
            </>
          )}
        </div>
      )}

      {active === "details" && result && (
        <div role="tabpanel" id="wb-panel-details" aria-labelledby="wb-tab-details" className="wb-details">
          <dl>
            <dt>Ran on</dt>
            <dd>{result.savedAs ? "This conversation's result tables (DuckDB)" : connectionName}</dd>
            <dt>Rows</dt>
            <dd>{result.meta.rows.toLocaleString()}{result.meta.truncated && " (partial: row limit reached)"}</dd>
            {!result.savedAs && (
              <>
                <dt>Bytes scanned</dt>
                <dd>
                  {formatBytes(result.meta.bytes_processed)}
                  {result.meta.bytes_processed === 0 && (result.meta.job_id ? " (served from the warehouse's cache)" : " (free preview: stored rows, no query)")}
                </dd>
                <dt>Estimated before running</dt>
                <dd>{formatBytes(result.bytes_estimated)}</dd>
              </>
            )}
            <dt>Duration</dt>
            <dd>{result.meta.duration_ms.toLocaleString()} ms</dd>
            {result.meta.job_id && (
              <>
                <dt>Job ID</dt>
                <dd><code>{result.meta.job_id}</code></dd>
              </>
            )}
            {result.tables.length > 0 && (
              <>
                <dt>Tables read</dt>
                <dd>{result.tables.map((t) => <code key={t}>{t}</code>)}</dd>
              </>
            )}
          </dl>
          {ranSql && (
            <>
              <h4>SQL that ran</h4>
              <pre className="wb-details-sql">{ranSql.trim()}</pre>
            </>
          )}
        </div>
      )}

      {active === "fields" && fields && (
        <div role="tabpanel" id="wb-panel-fields" aria-labelledby="wb-tab-fields">
          {fields}
        </div>
      )}
    </section>
  );
}
