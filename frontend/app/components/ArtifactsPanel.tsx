"use client";

import { useCallback, useEffect, useRef, useState, type ChangeEvent } from "react";

import { downloadSvgAsPng } from "@/lib/chartExport";
import { describeUpload, FILES_CHANGED, UPLOAD_ACCEPT, uploadFile } from "@/lib/uploads";

import Chart, { type ChartData } from "./Chart";
import { conversationTitle, type Conversation } from "./Conversations";
import ResultsGrid from "./ResultsGrid";

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

const FILTERS: { id: Kind | "all"; label: string }[] = [
  { id: "all", label: "All" },
  { id: "table", label: "Tables" },
  { id: "chart", label: "Charts" },
  { id: "file", label: "Files" },
];

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

/** Artifacts tab: every table, chart and uploaded file the user has, across conversations. */
export default function ArtifactsPanel({
  sessionId,
  conversations,
  revision,
  onOpenConversation,
}: {
  sessionId: string;
  conversations: Conversation[] | null;
  revision: number;
  onOpenConversation: (threadId: string) => void;
}) {
  const [filter, setFilter] = useState<Kind | "all">("all");
  const [thisConversation, setThisConversation] = useState(false);
  const [items, setItems] = useState<StoreItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploadStatus, setUploadStatus] = useState<{ ok: boolean; text: string } | null>(null);
  const [uploading, setUploading] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const load = useCallback(() => {
    const q = new URLSearchParams();
    if (filter !== "all") q.set("kind", filter);
    if (thisConversation) q.set("session_id", sessionId);
    fetch(`/api/odn/artifacts?${q}`, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((d: StoreItem[]) => {
        setItems(d);
        setError(null);
      })
      .catch(() => setError("Could not load your artifacts."));
  }, [filter, thisConversation, sessionId]);

  useEffect(load, [load, revision]);
  useEffect(() => {
    window.addEventListener(FILES_CHANGED, load);
    return () => window.removeEventListener(FILES_CHANGED, load);
  }, [load]);

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
      {items === null && !error && <p className="odn-panel-note">Loading…</p>}
      {items?.length === 0 && (
        <p className="odn-panel-note">
          {thisConversation
            ? "Nothing in this conversation yet. Query results, charts and uploads will collect here."
            : "No artifacts yet. Query results, charts and files you upload will collect here."}
        </p>
      )}
      <ul className="odn-artifact-list" aria-label="Artifacts">
        {items?.map((item) => (
          <ArtifactCard
            key={`${item.kind}-${item.id}`}
            item={item}
            conversation={bySession.get(item.session_id)}
            isCurrent={item.session_id === sessionId}
            onOpenConversation={onOpenConversation}
          />
        ))}
      </ul>
    </div>
  );
}
