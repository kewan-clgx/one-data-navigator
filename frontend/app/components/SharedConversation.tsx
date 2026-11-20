"use client";

import { Markdown } from "@copilotkit/react-ui";
import { useEffect, useState } from "react";

import { api } from "@/lib/client-api";

import { StepCard } from "./AgentSteps";
import Chart, { type ChartData } from "./Chart";
import ResultsGrid from "./ResultsGrid";

type ToolCall = { id: string; function: { name: string; arguments: string } };
type Message = { id: string; role: "user" | "assistant" | "tool"; content?: string; toolCalls?: ToolCall[]; toolCallId?: string };
type Artifact =
  | { kind: "table"; name: string; sql: string; row_count: number; truncated: boolean; columns: ({ name: string } | string)[] }
  | { kind: "chart"; name: string; title: string; result: ChartData }
  | { kind: "file"; id: string; name: string; mime_type: string; size_bytes: number };
export type Share = {
  id: string; kind: "conversation" | "answer"; title: string; owner_name: string | null; owner_email: string; created_at: string; expires_at: string;
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
  const open = share.kind === "answer";
  if (!share.artifacts.length) {
    return share.include_results ? null : <p className="muted">Results weren&apos;t included in this share (questions, answers and SQL only).</p>;
  }
  return (
    <section className="shared-artifacts" aria-label="Results">
      {share.kind !== "answer" && <h2>Results</h2>}
      {share.artifacts.map((a) =>
        a.kind === "table" ? (
          <details key={a.name} className="shared-artifact" open={open}>
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
        ) : (
          <p key={a.id} className="shared-artifact">
            📎 <a href={`/api/odn/shares/${share.id}/files/${a.id}`} download={a.name}>{a.name}</a>{" "}
            <span className="muted">({Math.ceil(a.size_bytes / 1024).toLocaleString()} KB)</span>
          </p>
        ),
      )}
    </section>
  );
}

/** Read-only view of a conversation someone shared with the signed-in user. */
export default function SharedConversation({ shareId }: { shareId: string }) {
  const [share, setShare] = useState<Share | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    document.cookie = "odn_return=; Max-Age=0; Path=/; SameSite=Lax";
    api<Share>(`shares/${shareId}`).then(setShare).catch((e: Error) => setError(e.message));
  }, [shareId]);

  if (error) {
    return (
      <div className="shared-page">
        <h1>Can&apos;t open this conversation</h1>
        <p>{error}</p>
        <p><a className="odn-btn" href="/">Go to QueryMuse</a></p>
      </div>
    );
  }
  if (!share) return <div className="shared-page"><p className="muted">Loading…</p></div>;

  const owner = share.owner_name || share.owner_email;
  return (
    <div className="shared-page">
      <header className="shared-head">
        <p className="shared-kicker">{share.kind === "answer" ? "Shared answer" : "Shared conversation"}</p>
        <h1>{share.title}</h1>
        <p className="muted">
          Shared by {share.is_owner ? "you" : owner} on {new Date(share.created_at).toLocaleDateString()} ·{" "}
          {share.status === "active" ? `link works until ${new Date(share.expires_at).toLocaleDateString()}` : `link ${share.status}`}
        </p>
        <p className="muted">
          {share.kind === "answer"
            ? "A question and its answer from a QueryMuse conversation, as it was when shared."
            : "A read-only copy of the conversation as it was when shared."}
        </p>
        <div className="settings-actions">
          <a className="odn-btn odn-btn--outline" href="/">Back to QueryMuse</a>
        </div>
      </header>
      <Transcript messages={share.messages} />
      <Artifacts share={share} />
    </div>
  );
}
