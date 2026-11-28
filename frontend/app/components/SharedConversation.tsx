"use client";

import { Markdown } from "@copilotkit/react-ui";
import Link from "next/link";
import { useEffect, useState } from "react";

import { api } from "@/lib/client-api";
import { questionAndAnswer, type PublicArtifact, type PublicShare } from "@/lib/share";

import { StepCard } from "./AgentSteps";
import Chart, { type ChartData } from "./Chart";
import PublicShareView, { FollowUpButton, SummaryReport } from "./PublicShareView";
import ResultsGrid from "./ResultsGrid";
import SqlCode from "./SqlCode";
import type { SqlDialect } from "@/lib/savedSql";

type ToolCall = { id: string; function: { name: string; arguments: string } };
type Message = { id: string; role: "user" | "assistant" | "tool"; content?: string; toolCalls?: ToolCall[]; toolCallId?: string };
type Artifact =
  | { kind: "table"; name: string; sql: string; row_count: number; truncated: boolean; columns: ({ name: string } | string)[] }
  | { kind: "chart"; name: string; title: string; result: ChartData }
  | { kind: "file"; id: string; name: string; mime_type: string; size_bytes: number }
  | { kind: "sql"; id: string; name: string; sql: string; dialect: SqlDialect; tables: string[]; description: string };
export type Share = {
  id: string; kind: "conversation" | "answer" | "artifact"; title: string; owner_name: string | null; owner_email: string; created_at: string; expires_at: string;
  status: "active" | "expired" | "revoked"; include_results: boolean; is_owner: boolean; url: string;
  messages: Message[]; artifacts: Artifact[];
};

const parseArgs = (s: string) => {
  try {
    return JSON.parse(s);
  } catch {
    return s;
  }
};

function Transcript({ messages }: { messages: Message[] }) {
  const results = new Map(messages.filter((m) => m.role === "tool").map((m) => [m.toolCallId, m.content]));
  return (
    <ol className="shared-transcript" aria-label="Conversation">
      {messages.filter((m) => m.role !== "tool").map((m) => (
        <li key={m.id} className={`shared-msg shared-msg--${m.role}`}>
          {m.content?.trim() && (m.role === "user" ? <p className="shared-user">{m.content}</p> : <Markdown content={m.content} />)}
          {m.toolCalls?.map((c) => (
            <StepCard key={c.id} readOnly name={c.function.name} status="complete" args={parseArgs(c.function.arguments)} result={results.get(c.id)} />
          ))}
        </li>
      ))}
    </ol>
  );
}

function Artifacts({ share }: { share: Share }) {
  if (!share.artifacts.length) {
    return share.include_results ? null : <p className="muted">Results weren&apos;t included in this share (questions, answers and SQL only).</p>;
  }
  return (
    <section className="shared-artifacts" aria-label="Results">
      <h2>Results</h2>
      {share.artifacts.map((a) =>
        a.kind === "table" ? (
          <details key={a.name} className="shared-artifact">
            <summary><strong>{a.name}</strong> · {a.row_count.toLocaleString()} {a.row_count === 1 ? "row" : "rows"}{a.truncated ? " (partial)" : ""}</summary>
            <pre>{a.sql}</pre>
            <ResultsGrid
              sessionId={share.id}
              name={a.name}
              columns={a.columns.map((c) => (typeof c === "string" ? c : c.name))}
              initialRows={[]}
              rowsBase={`/api/odn/shares/${share.id}/tables/${a.name}`}
            />
          </details>
        ) : a.kind === "chart" ? (
          <details key={a.name} className="shared-artifact" open>
            <summary><strong>{a.name}</strong> · {a.title || "Chart"}</summary>
            <Chart data={a.result} />
          </details>
        ) : a.kind === "file" ? (
          <p key={a.id} className="shared-artifact">
            📎 <a href={`/api/odn/shares/${share.id}/files/${a.id}`} download={a.name}>{a.name}</a>{" "}
            <span className="muted">({Math.ceil(a.size_bytes / 1024).toLocaleString()} KB)</span>
          </p>
        ) : null,
      )}
    </section>
  );
}

/** An answer share for a signed-in reader: the same short report as the public link (first rows of tables). */
function AnswerReport({ share }: { share: Share }) {
  const [artifacts, setArtifacts] = useState<PublicArtifact[] | null>(null);
  useEffect(() => {
    Promise.all(
      share.artifacts.map(async (a): Promise<PublicArtifact | null> => {
        if (a.kind === "chart") return { kind: "chart", name: a.name, title: a.title, result: a.result };
        if (a.kind !== "table") return null;
        const columns = a.columns.map((c) => (typeof c === "string" ? c : c.name));
        const page = await api<{ rows: Record<string, unknown>[] }>(`shares/${share.id}/tables/${a.name}/rows?limit=50`)
          .catch(() => ({ rows: [] }));
        return { kind: "table", name: a.name, row_count: a.row_count, columns, rows: page.rows };
      }),
    ).then((items) => setArtifacts(items.filter((x): x is PublicArtifact => x !== null)));
  }, [share]);
  const { question, answer } = questionAndAnswer(share.messages);
  return (
    <SummaryReport
      shareId={share.id} title={share.title} question={question} answer={answer}
      owner={share.is_owner ? "you" : share.owner_name || share.owner_email} createdAt={share.created_at}
      expiresAt={share.expires_at} status={share.status} artifacts={artifacts ?? []} signedIn
    />
  );
}

const ARTIFACT_LABEL = { sql: "SQL", table: "table", chart: "chart", file: "file" } as const;

/** One shared table, chart, file or saved SQL (a frozen copy), for a signed-in reader. */
function ArtifactShare({ share }: { share: Share }) {
  const a = share.artifacts[0];
  const [saved, setSaved] = useState<{ name: string } | null>(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const owner = share.is_owner ? "you" : share.owner_name || share.owner_email;
  const description = share.messages.find((m) => m.role === "assistant")?.content ?? "";
  async function saveSql() {
    setError(null);
    try {
      setSaved(await api<{ name: string }>(`shares/${share.id}/save-sql`, { method: "POST" }));
    } catch (e) {
      setError((e as Error).message);
    }
  }
  return (
    <div className="shared-page">
      <header className="shared-head">
        <p className="shared-kicker">Shared {a ? ARTIFACT_LABEL[a.kind] : "item"}</p>
        <h1>{share.title}</h1>
        <p className="muted">
          Shared by {owner} on {new Date(share.created_at).toLocaleDateString()} ·{" "}
          {share.status === "active" ? `link works until ${new Date(share.expires_at).toLocaleDateString()}` : `link ${share.status}`}
        </p>
        {description && <div className="shared-artifact-desc"><Markdown content={description} /></div>}
      </header>
      {share.status !== "active" ? (
        <p className="muted">This link has {share.status === "revoked" ? "been revoked" : "expired"}.</p>
      ) : !a ? null : a.kind === "sql" ? (
        <section className="shared-artifact" aria-label="SQL">
          <SqlCode text={a.sql} dialect={a.dialect} label={`${a.name}.sql`} />
          <div className="settings-actions">
            <button type="button" className="odn-btn odn-btn--outline" onClick={() => void navigator.clipboard.writeText(a.sql).then(() => setCopied(true))}>
              {copied ? "Copied ✓" : "Copy SQL"}
            </button>
            {saved ? (
              <span className="muted">Saved as <code>{saved.name}.sql</code> in your Artifacts. <Link href="/">Open EasyQuery</Link></span>
            ) : (
              <button type="button" className="odn-btn" onClick={() => void saveSql()}>Save to my SQL</button>
            )}
          </div>
          {error && <p className="error" role="alert">{error}</p>}
        </section>
      ) : a.kind === "table" ? (
        <section className="shared-artifact" aria-label="Table">
          <ResultsGrid
            sessionId={share.id} name={a.name} columns={a.columns.map((c) => (typeof c === "string" ? c : c.name))}
            initialRows={[]} rowsBase={`/api/odn/shares/${share.id}/tables/${a.name}`}
          />
          <details className="shared-artifact-sql"><summary>SQL</summary><SqlCode text={a.sql} label="SQL" /></details>
        </section>
      ) : a.kind === "chart" ? (
        <section className="shared-artifact" aria-label="Chart"><Chart data={a.result} /></section>
      ) : (
        <p className="shared-artifact">
          📎 <a href={`/api/odn/shares/${share.id}/files/${a.id}`} download={a.name}>{a.name}</a>{" "}
          <span className="muted">({Math.ceil(a.size_bytes / 1024).toLocaleString()} KB)</span>
        </p>
      )}
      <p><Link className="odn-btn odn-btn--outline" href="/">Back to EasyQuery</Link></p>
    </div>
  );
}

/** Read-only view of a conversation someone shared with the signed-in user. */
export default function SharedConversation({ shareId, fallback }: { shareId: string; fallback?: PublicShare | null }) {
  const [share, setShare] = useState<Share | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    document.cookie = "odn_return=; Max-Age=0; Path=/; SameSite=Lax";
    api<Share>(`shares/${shareId}`).then(setShare).catch((e: Error) => setError(e.message));
  }, [shareId]);

  // Signed in to another organization: the "anyone with the link" summary is still readable.
  if (error && fallback) return <PublicShareView share={fallback} signedIn />;
  if (error) {
    return (
      <div className="shared-page">
        <h1>Can&apos;t open this conversation</h1>
        <p>{error}</p>
        <p><a className="odn-btn" href="/">Go to EasyQuery</a></p>
      </div>
    );
  }
  if (!share) return <div className="shared-page"><p className="muted">Loading…</p></div>;
  if (share.kind === "answer") return <AnswerReport share={share} />;
  if (share.kind === "artifact") return <ArtifactShare share={share} />;

  const owner = share.owner_name || share.owner_email;
  return (
    <div className="shared-page">
      <header className="shared-head">
        <p className="shared-kicker">Shared conversation</p>
        <h1>{share.title}</h1>
        <p className="muted">
          Shared by {share.is_owner ? "you" : owner} on {new Date(share.created_at).toLocaleDateString()} ·{" "}
          {share.status === "active" ? `link works until ${new Date(share.expires_at).toLocaleDateString()}` : `link ${share.status}`}
        </p>
        <p className="muted">A read-only copy of the conversation as it was when shared.</p>
        <div className="settings-actions">
          <a className="odn-btn odn-btn--outline" href="/">Back to EasyQuery</a>
        </div>
      </header>
      <Transcript messages={share.messages} />
      <Artifacts share={share} />
      {share.status === "active" && <FollowUpButton shareId={share.id} signedIn />}
    </div>
  );
}
