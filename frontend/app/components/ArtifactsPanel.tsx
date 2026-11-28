"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useId, useRef, useState, type ChangeEvent, type ReactNode, type RefObject } from "react";

import { downloadSvgAsPng } from "@/lib/chartExport";
import { describeUpload, FILES_CHANGED, UPLOAD_ACCEPT, uploadFile } from "@/lib/uploads";
import {
  NameTakenError, notifySavedSqlChanged, SAVED_SQL_CHANGED, savedSql, type SavedQuery, type SqlDialect,
} from "@/lib/savedSql";
import { DIALECT_LABEL } from "@/lib/sqlSyntax";

import Chart, { type ChartData } from "./Chart";
import { conversationTitle, type Conversation } from "./Conversations";
import ResultsGrid from "./ResultsGrid";
import ArtifactShareDialog, { type ShareTarget } from "./ArtifactShareDialog";
import SaveSqlPopover from "./SaveSqlPopover";

const SqlCode = dynamic(() => import("./SqlCode"), { ssr: false, loading: () => <p className="muted">Loading…</p> });

type Kind = "table" | "chart" | "file" | "sql";

/** One Artifacts panel item (GET /v1/artifacts/page). */
type StoreItem = {
  id: string;
  kind: Kind;
  session_id: string;
  name: string;
  title: string;
  created_at: string;
  origin?: "warehouse" | "workspace" | "upload" | null;
  row_count?: number | null;
  column_count?: number | null;
  column_names?: string[];
  source_tables: string[];
  chart_type?: string | null;
  point_count?: number | null;
  mime_type?: string | null;
  size_bytes?: number | null;
  table_name?: string | null;
  inline: boolean;
  // tables and saved SQL: the first lines of the SQL
  sql_head?: string | null;
  sql_lines?: number | null;
  // saved SQL
  description?: string | null;
  dialect?: SqlDialect | null;
  version?: number | null;
  tables?: string[];
  // archived items (the Archived view)
  archived_at?: string | null;
  purge_at?: string | null;
};
type Page = { items: StoreItem[]; next_cursor: string | null; counts: Partial<Record<Kind, number>> | null };

type Filter = Kind | "all";
type Sort = "newest" | "name";

const FILTERS: { id: Filter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "sql", label: "SQL" },
  { id: "table", label: "Tables" },
  { id: "chart", label: "Charts" },
  { id: "file", label: "Files" },
];
const PAGE_SIZE = 20;
const PREVIEW_ROWS = 10;

/** Opens the Artifacts tab on a filter and search (Explorer "Saved SQL (n) ›"). */
export type ArtifactsRequest = { kind: Filter; q: string; nonce: number };

const KIND_LABEL: Record<Kind, string> = { table: "Table", chart: "Chart", file: "File", sql: "SQL" };

/** True once the element has come near the viewport (stays true): previews load only for cards people reach. */
function useInView<T extends Element>(): [RefObject<T>, boolean] {
  const ref = useRef<T>(null) as RefObject<T>;
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || seen) return;
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) {
        setSeen(true);
        io.disconnect();
      }
    }, { rootMargin: "200px 0px" });
    io.observe(el);
    return () => io.disconnect();
  }, [seen]);
  return [ref, seen];
}

function formatBytes(n: number | null | undefined): string {
  if (n == null) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 ** 2).toFixed(1)} MB`;
}

function when(iso: string): string {
  const d = new Date(iso);
  return d.toDateString() === new Date().toDateString()
    ? d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })
    : d.toLocaleDateString([], { month: "short", day: "numeric" });
}

const fileUrl = (item: StoreItem, inline = false) =>
  `/api/odn/artifacts/files/${encodeURIComponent(item.id)}${inline ? "?inline=true" : ""}`;
const csvUrl = (item: StoreItem) =>
  `/api/odn/workspace/${encodeURIComponent(item.session_id)}/artifacts/${encodeURIComponent(item.name)}/csv`;

type Keep = "30d" | "forever";
/** What a card can ask the panel to do; the panel owns undo, the share dialog and reloading. */
type Actions = {
  archivedView: boolean;
  onShare: (item: StoreItem) => void;
  onArchive: (item: StoreItem, keep: Keep) => void;
  onRestore: (item: StoreItem) => void;
  onDelete: (item: StoreItem) => void;
};

/** How an artifact is named in messages ("q1", "revenue.sql", the chart's title, the file name). */
export function itemLabel(item: StoreItem): string {
  if (item.kind === "sql") return `${item.name}.sql`;
  if (item.kind === "chart") return item.title || item.name;
  return item.name;
}

function shareTarget(item: StoreItem): ShareTarget {
  const title = item.kind === "sql" ? `${item.name}.sql` : item.kind === "table" ? `Result table ${item.name}` : item.title || item.name;
  return { kind: item.kind, id: item.id, title };
}

/** "Archived · deleted on Dec 28" / "Archived · kept until you delete it". */
function ArchivedNote({ item }: { item: StoreItem }) {
  if (!item.archived_at) return null;
  return (
    <div className="odn-artifact-meta odn-artifact-archived">
      Archived {when(item.archived_at)} ·{" "}
      {item.purge_at ? `deleted for good on ${new Date(item.purge_at).toLocaleDateString()}` : "kept until you delete it"}
    </div>
  );
}

/** ⋯ menu: Share, (extra items, e.g. Rename for SQL), Archive or Restore, Delete; Archive asks how long to keep it. */
function CardMenu({ item, actions, extra = [], inline = false }: {
  item: StoreItem; actions: Actions; extra?: { label: string; run: () => void }[];
  /** Plain buttons instead of a ⋯ menu (charts: next to Download PNG). */
  inline?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const [keep, setKeep] = useState<Keep>("30d");
  const ids = useId();
  const label = itemLabel(item);
  const close = () => setOpen(false);
  const archiveChoice = archiving && (
    <fieldset className="odn-artifact-archive" onKeyDown={(e) => e.key === "Escape" && setArchiving(false)}>
      <legend>Archive {label}? It leaves this list and search.</legend>
      <label>
        <input type="radio" name={`${ids}-keep`} checked={keep === "30d"} onChange={() => setKeep("30d")} />
        Keep 30 days, then delete it
      </label>
      <label>
        <input type="radio" name={`${ids}-keep`} checked={keep === "forever"} onChange={() => setKeep("forever")} />
        Keep it until I delete it
      </label>
      <span className="odn-artifact-archive-actions">
        <button type="button" className={`odn-btn odn-btn--small${inline ? "" : " odn-btn--on-panel"}`} onClick={() => { setArchiving(false); actions.onArchive(item, keep); }}>
          Archive
        </button>
        <button type="button" className={`odn-btn odn-btn--small ${inline ? "odn-btn--outline" : "odn-btn--ghost-panel"}`} onClick={() => setArchiving(false)}>
          Cancel
        </button>
      </span>
    </fieldset>
  );
  if (inline) {
    return (
      <>
        {actions.archivedView ? (
          <button type="button" className="odn-btn odn-btn--outline odn-btn--small" onClick={() => actions.onRestore(item)}>Restore</button>
        ) : (
          <>
            <button type="button" className="odn-btn odn-btn--outline odn-btn--small" onClick={() => actions.onShare(item)}>Share</button>
            <button type="button" className="odn-btn odn-btn--outline odn-btn--small" aria-expanded={archiving} onClick={() => setArchiving(!archiving)}>
              Archive
            </button>
          </>
        )}
        <button type="button" className="odn-btn odn-btn--outline odn-btn--small odn-btn--danger-text" onClick={() => actions.onDelete(item)}>
          Delete
        </button>
        {archiveChoice && <div className="odn-artifact-archive-row">{archiveChoice}</div>}
      </>
    );
  }
  return (
    <>
      <span className="odn-artifact-more">
        <button
          type="button" className="odn-btn odn-btn--ghost-panel odn-btn--small" aria-haspopup="menu" aria-expanded={open}
          aria-label={`More actions for ${label}`} onClick={() => setOpen(!open)}
        >
          ⋯
        </button>
        {open && (
          <span className="odn-artifact-menu" role="menu" onKeyDown={(e) => e.key === "Escape" && close()}>
            {actions.archivedView ? (
              <button type="button" role="menuitem" onClick={() => { close(); actions.onRestore(item); }}>Restore</button>
            ) : (
              <>
                <button type="button" role="menuitem" onClick={() => { close(); actions.onShare(item); }}>Share…</button>
                {extra.map((x) => (
                  <button key={x.label} type="button" role="menuitem" onClick={() => { close(); x.run(); }}>{x.label}</button>
                ))}
                <button type="button" role="menuitem" onClick={() => { close(); setArchiving(true); }}>Archive…</button>
              </>
            )}
            <button type="button" role="menuitem" className="danger" onClick={() => { close(); actions.onDelete(item); }}>
              Delete
            </button>
          </span>
        )}
      </span>
      {archiveChoice}
    </>
  );
}

/** SQL shown by default: the first 10 lines, "Show all N lines" loads the rest. */
function SqlPreview({ head, lines, dialect, label, loadFull }: {
  head: string; lines: number; dialect?: SqlDialect | null; label: string; loadFull: () => Promise<string | null>;
}) {
  const [full, setFull] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const more = lines > head.split("\n").length;
  async function expand() {
    if (!full) {
      const text = await loadFull();
      if (!text) return;
      setFull(text);
    }
    setExpanded(true);
  }
  return (
    <div className="odn-artifact-sql">
      <SqlCode text={expanded && full ? full : head} dialect={dialect ?? undefined} label={label} />
      {more && (
        <button type="button" className="odn-linklike odn-artifact-expand" onClick={() => (expanded ? setExpanded(false) : void expand())}>
          {expanded ? "Show fewer lines" : `Show all ${lines.toLocaleString()} lines`}
        </button>
      )}
    </div>
  );
}

/** The first 10 rows; "Show more rows" opens the full paged, sortable grid. */
function TablePreview({ item }: { item: StoreItem }) {
  const [full, setFull] = useState(false);
  const [columns, setColumns] = useState<string[] | null>(item.column_names?.length ? item.column_names : null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (columns) return;
    fetch(`/api/odn/artifacts/tables/${encodeURIComponent(item.id)}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((a: { columns: { name: string }[] }) => setColumns(a.columns.map((c) => c.name)))
      .catch(() => setError("Could not load this table."));
  }, [item.id, columns]);
  if (error) return <p className="error">{error}</p>;
  if (!columns) return <p className="muted">Loading…</p>;
  const rows = item.row_count ?? 0;
  return (
    <>
      <ResultsGrid
        key={full ? "full" : "preview"} sessionId={item.session_id} name={item.name} columns={columns} initialRows={[]}
        pageSize={full ? 100 : PREVIEW_ROWS} preview={!full}
      />
      {rows > PREVIEW_ROWS && (
        <button type="button" className="odn-linklike odn-artifact-expand" onClick={() => setFull(!full)}>
          {full ? `Show ${PREVIEW_ROWS} rows` : `Show more rows (${rows.toLocaleString()})`}
        </button>
      )}
    </>
  );
}

function ChartPreview({ item, children }: { item: StoreItem; children?: ReactNode }) {
  const [data, setData] = useState<ChartData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    fetch(`/api/odn/artifacts/charts/${encodeURIComponent(item.id)}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then(setData)
      .catch(() => setError("Could not load this chart."));
  }, [item.id]);
  if (error) return <p className="error">{error}</p>;
  if (!data) return <p className="muted">Loading chart…</p>;
  return (
    <>
      <div ref={ref}>
        <Chart data={data} />
      </div>
      <div className="odn-artifact-chart-actions">
        <button
          type="button"
          className="odn-btn odn-btn--small"
          onClick={() => {
            const svg = ref.current?.querySelector("svg");
            if (svg) downloadSvgAsPng(svg, `${item.title || item.name}.png`).catch((e: Error) => setError(e.message));
          }}
        >
          Download PNG
        </button>
        {children}
      </div>
    </>
  );
}

async function tableSql(item: StoreItem): Promise<string | null> {
  const r = await fetch(`/api/odn/artifacts/tables/${encodeURIComponent(item.id)}`);
  if (!r.ok) return null;
  return ((await r.json()) as { sql_text?: string }).sql_text ?? null;
}

/** Result table, chart or file, with its preview open by default (loaded when the card scrolls into view). */
function ArtifactCard({
  item,
  conversation,
  isCurrent,
  onOpenConversation,
  actions,
}: {
  item: StoreItem;
  conversation: Conversation | undefined;
  isCurrent: boolean;
  onOpenConversation: (threadId: string) => void;
  actions: Actions;
}) {
  const [ref, inView] = useInView<HTMLLIElement>();
  const [showSql, setShowSql] = useState(false);
  const isImage = item.kind === "file" && item.inline;

  const meta =
    item.kind === "table"
      ? `${(item.row_count ?? 0).toLocaleString()} rows · ${item.column_count ?? 0} columns`
      : item.kind === "chart"
        ? `${item.chart_type} chart · ${item.point_count ?? 0} points`
        : [formatBytes(item.size_bytes), item.table_name && `imported as table ${item.table_name}`]
            .filter(Boolean)
            .join(" · ");

  return (
    <li className="odn-artifact" ref={ref}>
      <div className="odn-artifact-head">
        <span className={`odn-artifact-kind odn-artifact-kind--${item.kind}`}>{KIND_LABEL[item.kind]}</span>
        <span className="odn-artifact-name" title={item.title}>
          {item.kind === "file" ? item.name : <><code>{item.name}</code> {item.title}</>}
        </span>
        <span className="odn-artifact-when">{when(item.created_at)}</span>
      </div>
      <div className="odn-artifact-meta">
        {meta}
        {item.source_tables.length > 0 && item.origin !== "upload" && (
          <span title={item.source_tables.join(", ")}> · {item.source_tables.map(shortTable).join(", ")}</span>
        )}
      </div>
      <div className="odn-artifact-meta">
        {isCurrent ? (
          "This conversation"
        ) : conversation ? (
          <>
            From{" "}
            <button type="button" className="odn-artifact-link" onClick={() => onOpenConversation(conversation.threadId)}>
              {conversationTitle(conversation)}
            </button>
          </>
        ) : (
          "From a deleted conversation"
        )}
      </div>
      <ArchivedNote item={item} />
      {(item.kind !== "file" || isImage) && (
        <div className="odn-artifact-preview">
          {!inView ? (
            <div className="odn-artifact-placeholder" aria-hidden="true" />
          ) : (
            <>
              {item.kind === "table" && <TablePreview item={item} />}
              {item.kind === "chart" && (
                <ChartPreview item={item}><CardMenu item={item} actions={actions} inline /></ChartPreview>
              )}
              {/* eslint-disable-next-line @next/next/no-img-element */}
              {isImage && <img src={fileUrl(item, true)} alt={item.name} loading="lazy" />}
            </>
          )}
        </div>
      )}
      {showSql && item.sql_head && (
        <div className="odn-artifact-preview">
          <SqlPreview head={item.sql_head} lines={item.sql_lines ?? 0} label={`SQL of ${item.name}`} loadFull={() => tableSql(item)} />
        </div>
      )}
      <div className="odn-artifact-actions">
        {item.kind === "table" && item.sql_head && (
          <button type="button" className="odn-btn odn-btn--ghost-panel odn-btn--small" aria-expanded={showSql} onClick={() => setShowSql(!showSql)}>
            {showSql ? "Hide SQL" : "SQL"}
          </button>
        )}
        {item.kind === "table" && (
          <a className="odn-btn odn-btn--ghost-panel odn-btn--small" href={csvUrl(item)} download={`${item.name}.csv`}>
            Download CSV
          </a>
        )}
        {item.kind === "file" && (
          <a className="odn-btn odn-btn--ghost-panel odn-btn--small" href={fileUrl(item)} download={item.name}>
            Download
          </a>
        )}
        {item.kind !== "chart" && <CardMenu item={item} actions={actions} />}
      </div>
    </li>
  );
}

function shortTable(t: string): string {
  return t.split(".").slice(-2).join(".");
}

/** A saved query: its first 10 lines shown; open it in the workbench (Save then overwrites it), or manage it. */
function SavedSqlCard({ item, onOpen, actions }: {
  item: StoreItem; onOpen: (q: SavedQuery) => void; actions: Actions;
}) {
  const [ref, inView] = useInView<HTMLLIElement>();
  const [full, setFull] = useState<SavedQuery | null>(null);
  const [renaming, setRenaming] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const tables = item.tables ?? [];

  async function load(): Promise<SavedQuery | null> {
    if (full && full.version === item.version && full.name === item.name) return full;
    try {
      const q = await savedSql.get(item.id);
      setFull(q);
      return q;
    } catch (e) {
      setNote((e as Error).message);
      return null;
    }
  }
  async function open() {
    const q = await load();
    if (q) onOpen(q);
  }
  async function duplicate() {
    const q = await load();
    if (!q) return;
    const body = { name: `${q.name}_copy`, sql: q.sql, dialect: q.dialect, connection_id: q.connection_id, description: q.description };
    try {
      await savedSql.create(body);
    } catch (e) {
      if (!(e instanceof NameTakenError)) {
        setNote((e as Error).message);
        return;
      }
      await savedSql.create({ ...body, name: e.suggestion });
    }
    notifySavedSqlChanged();
  }
  async function copy() {
    const q = await load();
    if (q) void navigator.clipboard?.writeText(q.sql).then(() => setNote("Copied to clipboard"));
  }

  return (
    <li className="odn-artifact" ref={ref}>
      <div className="odn-artifact-head">
        <span className="odn-artifact-kind odn-artifact-kind--sql">SQL</span>
        <span className="odn-artifact-name" title={`${item.name}.sql`}><code>{item.name}.sql</code></span>
        <span className="odn-artifact-when" title={`Edited ${new Date(item.created_at).toLocaleString()}`}>{when(item.created_at)}</span>
      </div>
      {item.description && <div className="odn-artifact-meta">{item.description}</div>}
      <div className="odn-artifact-meta" title={tables.join(", ")}>
        {item.dialect ? DIALECT_LABEL[item.dialect] ?? item.dialect : ""}
        {item.dialect === "duckdb" ? " · conversation results" : ""}
        {tables.length > 0 && ` · ${tables.slice(0, 3).map(shortTable).join(", ")}${tables.length > 3 ? ` +${tables.length - 3}` : ""}`}
        {(item.version ?? 1) > 1 && ` · v${item.version}`}
      </div>
      <ArchivedNote item={item} />
      {item.sql_head && (
        <div className="odn-artifact-preview">
          {inView ? (
            <SqlPreview
              head={item.sql_head} lines={item.sql_lines ?? 0} dialect={item.dialect} label={`${item.name}.sql`}
              loadFull={async () => (await load())?.sql ?? null}
            />
          ) : (
            <div className="odn-artifact-placeholder odn-artifact-placeholder--sql" aria-hidden="true" />
          )}
        </div>
      )}
      <div className="odn-artifact-actions">
        {!actions.archivedView && (
          <button type="button" className="odn-btn odn-btn--on-panel odn-btn--small" onClick={() => void open()}>
            Open in workbench
          </button>
        )}
        <a className="odn-btn odn-btn--ghost-panel odn-btn--small" href={savedSql.downloadUrl(item.id)} download={`${item.name}.sql`}>
          .sql
        </a>
        <CardMenu
          item={item} actions={actions}
          extra={[
            { label: "Rename", run: () => setRenaming(true) },
            { label: "Duplicate", run: () => void duplicate() },
            { label: "Copy SQL", run: () => void copy() },
          ]}
        />
      </div>
      {renaming && (
        <div className="odn-artifact-popover">
          <SaveSqlPopover
            title="Rename saved query" initialName={item.name} initialDescription={item.description ?? ""} submitLabel="Save"
            onSubmit={async (name, description) => {
              await savedSql.update(item.id, { version: item.version ?? 1, name, description });
              setRenaming(false);
              notifySavedSqlChanged();
            }}
            onClose={() => setRenaming(false)}
          />
        </div>
      )}
      {note && <p className="odn-panel-note" role="status">{note}</p>}
    </li>
  );
}

/** Artifacts tab: tables, charts, files and saved SQL across conversations, 20 at a time, previews open. */
export default function ArtifactsPanel({
  sessionId,
  conversations,
  revision,
  onOpenConversation,
  onOpenSql,
  request,
}: {
  sessionId: string;
  conversations: Conversation[] | null;
  revision: number;
  onOpenConversation: (threadId: string) => void;
  onOpenSql: (q: SavedQuery) => void;
  request?: ArtifactsRequest | null;
}) {
  const [filter, setFilter] = useState<Filter>("all");
  const [sort, setSort] = useState<Sort>("newest");
  const [thisConversation, setThisConversation] = useState(false);
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<StoreItem[] | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [counts, setCounts] = useState<Partial<Record<Kind, number>> | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [archivedView, setArchivedView] = useState(false);
  const [sharing, setSharing] = useState<ShareTarget | null>(null);
  // Hidden while a delete waits out its undo window; `notice` is the status line with its Undo.
  const [pendingDelete, setPendingDelete] = useState<StoreItem | null>(null);
  const [notice, setNotice] = useState<{ text: string; undo?: () => void } | null>(null);
  const [uploadStatus, setUploadStatus] = useState<{ ok: boolean; text: string } | null>(null);
  const [uploading, setUploading] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const deleteTimer = useRef<number | undefined>(undefined);
  const lastRequest = useRef<number | null>(null);
  const serial = useRef(0);
  const sentinel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const t = window.setTimeout(() => setQuery(search.trim()), 250);
    return () => window.clearTimeout(t);
  }, [search]);
  useEffect(() => {
    if (!request || request.nonce === lastRequest.current) return;
    lastRequest.current = request.nonce;
    setFilter(request.kind);
    setSearch(request.q);
    setQuery(request.q);
    setThisConversation(false);
  }, [request]);

  const fetchPage = useCallback((after: string | null) => {
    const q = new URLSearchParams({ sort, limit: String(PAGE_SIZE) });
    if (filter !== "all") q.set("kind", filter);
    if (thisConversation) q.set("session_id", sessionId);
    if (query) q.set("q", query);
    if (after) q.set("cursor", after);
    if (archivedView) q.set("archived", "true");
    return fetch(`/api/odn/artifacts/page?${q}`, { cache: "no-store" })
      .then((r) => (r.ok ? (r.json() as Promise<Page>) : Promise.reject(r.status)));
  }, [filter, sort, thisConversation, sessionId, query, archivedView]);

  // The first page (also after any change, save or upload): replaces the list.
  const load = useCallback(() => {
    const mine = ++serial.current;
    fetchPage(null)
      .then((page) => {
        if (mine !== serial.current) return;
        setItems(page.items);
        setCursor(page.next_cursor);
        setCounts(page.counts);
        setError(null);
      })
      .catch(() => mine === serial.current && setError("Could not load your artifacts."));
  }, [fetchPage]);

  const loadMore = useCallback(() => {
    if (!cursor || loadingMore) return;
    const mine = serial.current;
    setLoadingMore(true);
    fetchPage(cursor)
      .then((page) => {
        if (mine !== serial.current) return; // the list was reset meanwhile
        setItems((prev) => {
          const seen = new Set((prev ?? []).map((i) => `${i.kind}-${i.id}`));
          return [...(prev ?? []), ...page.items.filter((i) => !seen.has(`${i.kind}-${i.id}`))];
        });
        setCursor(page.next_cursor);
      })
      .catch(() => setError("Could not load more artifacts."))
      .finally(() => setLoadingMore(false));
  }, [cursor, loadingMore, fetchPage]);

  useEffect(load, [load, revision]);
  useEffect(() => {
    window.addEventListener(FILES_CHANGED, load);
    window.addEventListener(SAVED_SQL_CHANGED, load);
    return () => {
      window.removeEventListener(FILES_CHANGED, load);
      window.removeEventListener(SAVED_SQL_CHANGED, load);
    };
  }, [load]);

  // Infinite scroll: the next page loads when the end of the list comes near; "Load more" stays as a fallback.
  useEffect(() => {
    const el = sentinel.current;
    if (!el || !cursor) return;
    const io = new IntersectionObserver((entries) => entries.some((e) => e.isIntersecting) && loadMore(), {
      rootMargin: "300px 0px",
    });
    io.observe(el);
    return () => io.disconnect();
  }, [cursor, loadMore]);

  const itemUrl = (item: StoreItem, action = "") =>
    `/api/odn/artifacts/${item.kind}/${encodeURIComponent(item.id)}${action}`;
  const changed = (item: StoreItem) => (item.kind === "sql" ? notifySavedSqlChanged() : load());
  const commitDelete = useCallback((item: StoreItem) => {
    void fetch(itemUrl(item), { method: "DELETE" }).then((r) => {
      if (!r.ok && r.status !== 404) setError(`Couldn't delete ${itemLabel(item)}.`);
      setPendingDelete((p) => (p?.id === item.id ? null : p));
      changed(item);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load]);

  // Delete with a 5-second undo: the card hides at once, the delete happens when the undo window closes.
  function remove(item: StoreItem) {
    window.clearTimeout(deleteTimer.current);
    if (pendingDelete) commitDelete(pendingDelete);
    setPendingDelete(item);
    const timer = window.setTimeout(() => {
      commitDelete(item);
      setNotice(null);
    }, 5000);
    deleteTimer.current = timer;
    setNotice({
      text: `Deleted ${itemLabel(item)}.`,
      undo: () => {
        window.clearTimeout(timer);
        setPendingDelete(null);
        setNotice(null);
      },
    });
  }
  async function archive(item: StoreItem, keep: Keep) {
    const r = await fetch(itemUrl(item, "/archive"), {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ keep }),
    });
    if (!r.ok) {
      setError(`Couldn't archive ${itemLabel(item)}.`);
      return;
    }
    const { purge_at } = (await r.json()) as { purge_at: string | null };
    changed(item);
    setNotice({
      text: `Archived ${itemLabel(item)}: ${purge_at ? `deleted for good on ${new Date(purge_at).toLocaleDateString()}` : "kept until you delete it"}. Find it under Archived.`,
      undo: () => void restore(item, true),
    });
  }
  async function restore(item: StoreItem, undo = false) {
    const r = await fetch(itemUrl(item, "/restore"), { method: "POST" });
    if (!r.ok) {
      setError(`Couldn't restore ${itemLabel(item)}.`);
      return;
    }
    changed(item);
    setNotice(undo ? null : { text: `Restored ${itemLabel(item)}.` });
  }
  const actions: Actions = {
    archivedView, onShare: (item) => setSharing(shareTarget(item)), onArchive: (item, keep) => void archive(item, keep),
    onRestore: (item) => void restore(item), onDelete: remove,
  };

  async function onFiles(e: ChangeEvent<HTMLInputElement>) {
    const files = [...(e.target.files ?? [])];
    e.target.value = "";
    if (!files.length) return;
    setUploading(true);
    const messages: string[] = [];
    let ok = true;
    for (const file of files) {
      try {
        messages.push(describeUpload(await uploadFile(sessionId, file)));
      } catch (err) {
        ok = false;
        messages.push(`${file.name}: ${(err as Error).message}`);
      }
    }
    setUploading(false);
    setUploadStatus({ ok, text: messages.join(" ") });
  }

  const bySession = new Map((conversations ?? []).map((c) => [c.sessionId, c]));
  const visible = items?.filter((i) => !(i.kind === pendingDelete?.kind && i.id === pendingDelete.id));
  const count = (f: Filter) => {
    if (!counts) return null;
    const n = f === "all" ? Object.values(counts).reduce((a, b) => a + (b ?? 0), 0) : counts[f] ?? 0;
    return n;
  };

  return (
    <div className="odn-list-panel">
      <div className="odn-list-toolbar">
        <button
          type="button"
          className="odn-btn odn-btn--on-panel odn-btn--small"
          onClick={() => fileInput.current?.click()}
          disabled={uploading}
        >
          {uploading ? "Uploading…" : "Upload files"}
        </button>
        <input ref={fileInput} type="file" multiple accept={UPLOAD_ACCEPT} hidden onChange={onFiles} />
        <span className="odn-panel-note">CSV/XLSX become tables you can ask about.</span>
      </div>
      {uploadStatus && (
        <p className={uploadStatus.ok ? "odn-panel-status" : "error"} role="status">
          {uploadStatus.text}
        </p>
      )}
      <div className="odn-artifact-searchrow">
        <input
          type="search"
          className="odn-artifact-search"
          placeholder="Search names, SQL and tables"
          aria-label="Search artifacts"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          onKeyDown={(e) => e.key === "Escape" && setSearch("")}
        />
        <select className="odn-artifact-sort" aria-label="Sort" value={sort} onChange={(e) => setSort(e.target.value as Sort)}>
          <option value="newest">Newest</option>
          <option value="name">Name A–Z</option>
        </select>
      </div>
      <div className="odn-list-filters">
        <div className="odn-chip-row" role="group" aria-label="Show">
          {FILTERS.map((f) => {
            const n = count(f.id);
            return (
              <button
                key={f.id}
                type="button"
                className="odn-chip"
                aria-pressed={filter === f.id}
                onClick={() => setFilter(f.id)}
              >
                {f.label}
                {n !== null && <span className="odn-chip-count">{n.toLocaleString()}</span>}
              </button>
            );
          })}
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={thisConversation}
          className="switch"
          onClick={() => setThisConversation((v) => !v)}
        >
          <span aria-hidden className="switch-track">
            <span className="switch-thumb" />
          </span>
          This conversation only
        </button>
        <button
          type="button"
          role="switch"
          aria-checked={archivedView}
          className="switch"
          onClick={() => setArchivedView((v) => !v)}
          title="Archived items are hidden everywhere else and not searchable"
        >
          <span aria-hidden className="switch-track">
            <span className="switch-thumb" />
          </span>
          Archived
        </button>
      </div>
      {notice && (
        <p className="odn-panel-status" role="status">
          {notice.text}{" "}
          {notice.undo && <button type="button" className="odn-linklike" onClick={notice.undo}>Undo</button>}
        </p>
      )}
      {error && <p className="error" role="alert">{error}</p>}
      {items === null && !error && <p className="odn-panel-note">Loading…</p>}
      {visible?.length === 0 && (
        <p className="odn-panel-note">
          {query
            ? `Nothing matches “${query}”.`
            : archivedView
              ? "Nothing archived. Archive (in an item's ⋯ menu) hides it from this list and search, for 30 days or until you delete it."
              : filter === "sql"
              ? "No saved SQL yet. In the SQL workbench, Save (⌘S) keeps a query here with a name you choose; you can also save SQL from a chat answer."
              : thisConversation
                ? "Nothing in this conversation yet. Query results, charts and uploads will collect here."
                : "No artifacts yet. Query results, charts, saved SQL and files you upload will collect here."}
        </p>
      )}
      <ul className="odn-artifact-list" aria-label="Artifacts">
        {visible?.map((item) =>
          item.kind === "sql" ? (
            <SavedSqlCard key={`sql-${item.id}`} item={item} onOpen={onOpenSql} actions={actions} />
          ) : (
            <ArtifactCard
              key={`${item.kind}-${item.id}`}
              item={item}
              conversation={bySession.get(item.session_id)}
              isCurrent={item.session_id === sessionId}
              onOpenConversation={onOpenConversation}
              actions={actions}
            />
          ),
        )}
      </ul>
      {sharing && <ArtifactShareDialog target={sharing} onClose={() => setSharing(null)} />}
      {cursor && (
        <div ref={sentinel} className="odn-artifact-more-row">
          <button type="button" className="odn-btn odn-btn--ghost-panel odn-btn--small" onClick={loadMore} disabled={loadingMore}>
            {loadingMore ? "Loading…" : "Load more"}
          </button>
        </div>
      )}
      {!cursor && (visible?.length ?? 0) > PAGE_SIZE && (
        <p className="odn-panel-note odn-artifact-end">That&apos;s everything ({visible?.length.toLocaleString()}).</p>
      )}
    </div>
  );
}
