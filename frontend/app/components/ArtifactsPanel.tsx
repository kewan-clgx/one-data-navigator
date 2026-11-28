"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useRef, useState, type ChangeEvent } from "react";

import { downloadSvgAsPng } from "@/lib/chartExport";
import { describeUpload, FILES_CHANGED, UPLOAD_ACCEPT, uploadFile } from "@/lib/uploads";
import {
  NameTakenError, notifySavedSqlChanged, SAVED_SQL_CHANGED, savedSql, type SavedQuery, type SavedQueryItem,
} from "@/lib/savedSql";
import { DIALECT_LABEL } from "@/lib/sqlSyntax";

import Chart, { type ChartData } from "./Chart";
import { conversationTitle, type Conversation } from "./Conversations";
import ResultsGrid from "./ResultsGrid";
import SaveSqlPopover from "./SaveSqlPopover";

const SqlCode = dynamic(() => import("./SqlCode"), { ssr: false, loading: () => <p className="muted">Loading…</p> });

type Kind = "table" | "chart" | "file";

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
  source_tables: string[];
  chart_type?: string | null;
  point_count?: number | null;
  mime_type?: string | null;
  size_bytes?: number | null;
  table_name?: string | null;
  inline: boolean;
};

type Filter = Kind | "sql" | "all";

const FILTERS: { id: Filter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "sql", label: "SQL" },
  { id: "table", label: "Tables" },
  { id: "chart", label: "Charts" },
  { id: "file", label: "Files" },
];

/** Opens the Artifacts tab on a filter and search (Explorer "Saved SQL (n) ›"). */
export type ArtifactsRequest = { kind: Filter; q: string; nonce: number };
type Entry = { type: "store"; item: StoreItem; at: string } | { type: "sql"; item: SavedQueryItem; at: string };

const KIND_LABEL: Record<Kind, string> = { table: "Table", chart: "Chart", file: "File" };

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

function TablePreview({ item }: { item: StoreItem }) {
  const [columns, setColumns] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    fetch(`/api/odn/artifacts/tables/${encodeURIComponent(item.id)}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((a: { columns: { name: string }[] }) => setColumns(a.columns.map((c) => c.name)))
      .catch(() => setError("Could not load this table."));
  }, [item.id]);
  if (error) return <p className="error">{error}</p>;
  if (!columns) return <p className="muted">Loading…</p>;
  return <ResultsGrid sessionId={item.session_id} name={item.name} columns={columns} initialRows={[]} />;
}

function ChartPreview({ item }: { item: StoreItem }) {
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
  if (!data) return <p className="muted">Loading…</p>;
  return (
    <>
      <div ref={ref}>
        <Chart data={data} />
      </div>
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
    </>
  );
}

function ArtifactCard({
  item,
  conversation,
  isCurrent,
  onOpenConversation,
}: {
  item: StoreItem;
  conversation: Conversation | undefined;
  isCurrent: boolean;
  onOpenConversation: (threadId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const isImage = item.kind === "file" && item.inline;
  const canPreview = item.kind !== "file" || isImage;

  const meta =
    item.kind === "table"
      ? `${(item.row_count ?? 0).toLocaleString()} rows · ${item.column_count ?? 0} columns`
      : item.kind === "chart"
        ? `${item.chart_type} chart · ${item.point_count ?? 0} points`
        : [formatBytes(item.size_bytes), item.table_name && `imported as table ${item.table_name}`]
            .filter(Boolean)
            .join(" · ");

  return (
    <li className="odn-artifact">
      <div className="odn-artifact-head">
        <span className={`odn-artifact-kind odn-artifact-kind--${item.kind}`}>{KIND_LABEL[item.kind]}</span>
        <span className="odn-artifact-name" title={item.title}>
          {item.kind === "file" ? item.name : <><code>{item.name}</code> {item.title}</>}
        </span>
        <span className="odn-artifact-when">{when(item.created_at)}</span>
      </div>
      <div className="odn-artifact-meta">{meta}</div>
      {item.source_tables.length > 0 && item.origin !== "upload" && (
        <div className="odn-artifact-meta" title={item.source_tables.join(", ")}>
          Source: {item.source_tables.map((s) => s.split(".").slice(-2).join(".")).join(", ")}
        </div>
      )}
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
      <div className="odn-artifact-actions">
        {canPreview && (
          <button
            type="button"
            className="odn-btn odn-btn--ghost-panel odn-btn--small"
            aria-expanded={open}
            onClick={() => setOpen((o) => !o)}
          >
            {open ? "Hide preview" : "Preview"}
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
      </div>
      {open && (
        <div className="odn-artifact-preview">
          {item.kind === "table" && <TablePreview item={item} />}
          {item.kind === "chart" && <ChartPreview item={item} />}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          {isImage && <img src={fileUrl(item, true)} alt={item.name} />}
        </div>
      )}
    </li>
  );
}

function shortTable(t: string): string {
  return t.split(".").slice(-2).join(".");
}

/** A saved query: open it in the workbench (Save then overwrites it), preview, download, or manage it. */
function SavedSqlCard({ item, onOpen, onDelete }: {
  item: SavedQueryItem; onOpen: (q: SavedQuery) => void; onDelete: (item: SavedQueryItem) => void;
}) {
  const [full, setFull] = useState<SavedQuery | null>(null);
  const [preview, setPreview] = useState(false);
  const [menu, setMenu] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [note, setNote] = useState<string | null>(null);

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
    setMenu(false);
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
    setMenu(false);
    const q = await load();
    if (q) void navigator.clipboard?.writeText(q.sql).then(() => setNote("Copied to clipboard"));
  }

  return (
    <li className="odn-artifact">
      <div className="odn-artifact-head">
        <span className="odn-artifact-kind odn-artifact-kind--sql">SQL</span>
        <span className="odn-artifact-name" title={`${item.name}.sql`}><code>{item.name}.sql</code></span>
        <span className="odn-artifact-when" title={`Edited ${new Date(item.updated_at).toLocaleString()}`}>{when(item.updated_at)}</span>
      </div>
      {item.description && <div className="odn-artifact-meta">{item.description}</div>}
      <div className="odn-artifact-meta" title={item.tables.join(", ")}>
        {DIALECT_LABEL[item.dialect] ?? item.dialect}
        {item.dialect === "duckdb" ? " · conversation results" : ""}
        {item.tables.length > 0 && ` · ${item.tables.slice(0, 3).map(shortTable).join(", ")}${item.tables.length > 3 ? ` +${item.tables.length - 3}` : ""}`}
        {item.version > 1 && ` · v${item.version}`}
      </div>
      {!preview && item.snippet && <div className="odn-artifact-snippet">{item.snippet}</div>}
      <div className="odn-artifact-actions">
        <button type="button" className="odn-btn odn-btn--on-panel odn-btn--small" onClick={() => void open()}>
          Open in workbench
        </button>
        <button
          type="button" className="odn-btn odn-btn--ghost-panel odn-btn--small" aria-expanded={preview}
          onClick={() => { if (!preview) void load(); setPreview(!preview); }}
        >
          {preview ? "Hide SQL" : "Show SQL"}
        </button>
        <a className="odn-btn odn-btn--ghost-panel odn-btn--small" href={savedSql.downloadUrl(item.id)} download={`${item.name}.sql`}>
          .sql
        </a>
        <span className="odn-artifact-more">
          <button
            type="button" className="odn-btn odn-btn--ghost-panel odn-btn--small" aria-haspopup="menu" aria-expanded={menu}
            aria-label={`More actions for ${item.name}.sql`} onClick={() => setMenu(!menu)}
          >
            ⋯
          </button>
          {menu && (
            <span className="odn-artifact-menu" role="menu" onKeyDown={(e) => e.key === "Escape" && setMenu(false)}>
              <button type="button" role="menuitem" onClick={() => { setMenu(false); setRenaming(true); }}>Rename</button>
              <button type="button" role="menuitem" onClick={() => void duplicate()}>Duplicate</button>
              <button type="button" role="menuitem" onClick={() => void copy()}>Copy SQL</button>
              <button type="button" role="menuitem" className="danger" onClick={() => { setMenu(false); onDelete(item); }}>Delete</button>
            </span>
          )}
        </span>
      </div>
      {renaming && (
        <div className="odn-artifact-popover">
          <SaveSqlPopover
            title="Rename saved query" initialName={item.name} initialDescription={item.description} submitLabel="Save"
            onSubmit={async (name, description) => {
              await savedSql.update(item.id, { version: item.version, name, description });
              setRenaming(false);
              notifySavedSqlChanged();
            }}
            onClose={() => setRenaming(false)}
          />
        </div>
      )}
      {note && <p className="odn-panel-note" role="status">{note}</p>}
      {preview && (
        <div className="odn-artifact-preview">
          {full ? <SqlCode text={full.sql} dialect={full.dialect} label={`${item.name}.sql`} /> : <p className="muted">Loading…</p>}
        </div>
      )}
    </li>
  );
}

/** Artifacts tab: every table, chart and uploaded file the user has, across conversations. */
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
  const [thisConversation, setThisConversation] = useState(false);
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<Entry[] | null>(null);
  const [deleted, setDeleted] = useState<SavedQueryItem | null>(null);
  const deleteTimer = useRef<number | undefined>(undefined);
  const lastRequest = useRef<number | null>(null);

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
  const [error, setError] = useState<string | null>(null);
  const [uploadStatus, setUploadStatus] = useState<{ ok: boolean; text: string } | null>(null);
  const [uploading, setUploading] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const load = useCallback(() => {
    const q = new URLSearchParams();
    if (filter !== "all" && filter !== "sql") q.set("kind", filter);
    if (thisConversation) q.set("session_id", sessionId);
    // Saved SQL isn't tied to a conversation, so "This conversation only" leaves it out.
    const withSql = (filter === "all" || filter === "sql") && !thisConversation;
    const store: Promise<StoreItem[]> = filter === "sql" ? Promise.resolve([])
      : fetch(`/api/odn/artifacts?${q}`, { cache: "no-store" }).then((r) => (r.ok ? r.json() : Promise.reject(r.status)));
    const sql: Promise<SavedQueryItem[]> = withSql ? savedSql.list({ q: query }) : Promise.resolve([]);
    const needle = query.toLowerCase();
    const matches = (i: StoreItem) => !needle || [i.name, i.title, ...i.source_tables].some((t) => t.toLowerCase().includes(needle));
    Promise.all([store, sql])
      .then(([a, b]) => {
        const entries: Entry[] = [
          ...a.filter(matches).map((item) => ({ type: "store" as const, item, at: item.created_at })),
          ...b.map((item) => ({ type: "sql" as const, item, at: item.updated_at })),
        ];
        entries.sort((x, y) => y.at.localeCompare(x.at));
        setItems(entries);
        setError(null);
      })
      .catch(() => setError("Could not load your artifacts."));
  }, [filter, thisConversation, sessionId, query]);

  useEffect(load, [load, revision]);
  useEffect(() => {
    window.addEventListener(FILES_CHANGED, load);
    window.addEventListener(SAVED_SQL_CHANGED, load);
    return () => {
      window.removeEventListener(FILES_CHANGED, load);
      window.removeEventListener(SAVED_SQL_CHANGED, load);
    };
  }, [load]);

  // Delete with a 5-second undo: the card hides at once, the API call happens when the undo window closes.
  function remove(item: SavedQueryItem) {
    window.clearTimeout(deleteTimer.current);
    if (deleted) void savedSql.remove(deleted.id).then(notifySavedSqlChanged, () => undefined);
    setDeleted(item);
    deleteTimer.current = window.setTimeout(() => {
      void savedSql.remove(item.id).then(notifySavedSqlChanged, () => setError(`Couldn't delete ${item.name}.sql.`));
      setDeleted(null);
    }, 5000);
  }
  function undoDelete() {
    window.clearTimeout(deleteTimer.current);
    setDeleted(null);
  }
  const visible = items?.filter((e) => !(e.type === "sql" && e.item.id === deleted?.id));

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
      <input
        type="search"
        className="odn-search odn-artifact-search"
        placeholder="Search names, SQL and tables"
        aria-label="Search artifacts"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        onKeyDown={(e) => e.key === "Escape" && setSearch("")}
      />
      <div className="odn-list-filters">
        <div className="odn-chip-row" role="group" aria-label="Show">
          {FILTERS.map((f) => (
            <button
              key={f.id}
              type="button"
              className="odn-chip"
              aria-pressed={filter === f.id}
              onClick={() => setFilter(f.id)}
            >
              {f.label}
            </button>
          ))}
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
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      {deleted && (
        <p className="odn-panel-status" role="status">
          Deleted {deleted.name}.sql.{" "}
          <button type="button" className="odn-linklike" onClick={undoDelete}>Undo</button>
        </p>
      )}
      {items === null && !error && <p className="odn-panel-note">Loading…</p>}
      {visible?.length === 0 && (
        <p className="odn-panel-note">
          {query
            ? `Nothing matches “${query}”.`
            : filter === "sql"
              ? "No saved SQL yet. In the SQL workbench, Save (⌘S) keeps a query here with a name you choose; you can also save SQL from a chat answer."
              : thisConversation
                ? "Nothing in this conversation yet. Query results, charts and uploads will collect here."
                : "No artifacts yet. Query results, charts, saved SQL and files you upload will collect here."}
        </p>
      )}
      <ul className="odn-artifact-list" aria-label="Artifacts">
        {visible?.map((e) =>
          e.type === "sql" ? (
            <SavedSqlCard key={`sql-${e.item.id}`} item={e.item} onOpen={onOpenSql} onDelete={remove} />
          ) : (
            <ArtifactCard
              key={`${e.item.kind}-${e.item.id}`}
              item={e.item}
              conversation={bySession.get(e.item.session_id)}
              isCurrent={e.item.session_id === sessionId}
              onOpenConversation={onOpenConversation}
            />
          ),
        )}
      </ul>
    </div>
  );
}
