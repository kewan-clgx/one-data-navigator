"use client";

import { UseAgentUpdate, useAgent } from "@copilotkit/react-core/v2";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { AGENT_ID } from "./Results";

type Scope = { connectionId: string; path: string[] };
type TableInfo = { kind: "table" | "view"; row_count?: number | null; columns: unknown[]; modified_at?: string | null };
type Starter = { id: string; title: string; question: string };
type Answer = { answered_at: string; fresh: boolean; bytes_scanned?: number | null };
type DataQuestion = {
  id: string; title: string; question: string; source: "generated" | "asked" | "curated";
  popular: boolean; asked_by_you: boolean; hidden: boolean; private: boolean; answer: Answer | null;
};
type QuestionList = { status: "ready" | "generating" | "unavailable"; questions: DataQuestion[]; all?: DataQuestion[] | null };
type Handlers = { onPick: (question: string) => void; onOpenThread: (threadId: string) => void; canEdit: boolean };

const POLL_MS = 2000;
const POLL_LIMIT_MS = 25_000;

/**
 * The fixed "Get to know this table" questions (docs/table_questions_plan.md §2). Each is answered from stored
 * metadata (profile, modified time, definitions), so asking costs no warehouse scan. A view gets an "empty fields"
 * question instead of sample rows: previewing a view scans all of it.
 */
export function starterQuestions(name: string, kind: "table" | "view" | null): Starter[] {
  return [
    { id: "fill", title: "Fill rates", question: `What are the fill rates of the core fields in ${name}?` },
    { id: "updated", title: "Last updated", question: `When was ${name} last updated?` },
    { id: "core", title: "Core fields", question: `What are the 10 core fields in ${name}, and what do they mean?` },
    { id: "insights", title: "Possible insights", question: `What kinds of insights can I get from ${name}?` },
    kind === "table"
      ? { id: "sample", title: "Sample rows", question: `Show me 10 sample rows from ${name}.` }
      : { id: "empty", title: "Empty fields", question: `Which fields in ${name} are mostly empty?` },
  ];
}

function relative(iso: string): string {
  const days = Math.round((Date.now() - new Date(iso).getTime()) / 86_400_000);
  const fmt = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  if (Math.abs(days) < 1) return "today";
  if (Math.abs(days) < 45) return fmt.format(-days, "day");
  if (Math.abs(days) < 540) return fmt.format(-Math.round(days / 30), "month");
  return fmt.format(-Math.round(days / 365), "year");
}

/** True while the current conversation has no messages (a fresh or newly started thread). */
export function useConversationEmpty(): boolean {
  const { agent } = useAgent({ agentId: AGENT_ID, updates: [UseAgentUpdate.OnMessagesChanged] });
  return !agent || agent.messages.length === 0;
}

/**
 * Empty-conversation card for a scoped table: starter questions plus the table's data questions (suggested, asked by
 * teammates, curated). Picks pre-fill the chat input (never send), so the user decides when to spend tokens; a
 * question with a saved answer opens it instantly as a new conversation instead.
 */
export default function ScopedStarters({ scope, ...handlers }: { scope: Scope | null } & Handlers) {
  const empty = useConversationEmpty();
  return scope && empty ? <TableQuestions key={`${scope.connectionId}:${scope.path.join(".")}`} scope={scope} {...handlers} /> : null;
}

function questionsUrl(scope: Scope, suffix = ""): string {
  const q = scope.path.map((p) => `path=${encodeURIComponent(p)}`).join("&");
  return `/api/odn/connections/${encodeURIComponent(scope.connectionId)}/questions${suffix}?${q}`;
}

async function detail(r: Response): Promise<string> {
  try {
    const body = await r.json();
    return typeof body.detail === "string" ? body.detail : `Request failed (${r.status})`;
  } catch {
    return `Request failed (${r.status})`;
  }
}

/** Loads the table's data questions; while the suggested set is being generated, polls for a short while. */
function useDataQuestions(scope: Scope) {
  const [list, setList] = useState<QuestionList | null>(null);
  const [failed, setFailed] = useState(false);
  const started = useRef<number | null>(null);
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => {
    started.current = Date.now();
    setTick((t) => t + 1);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let timer: number | undefined;
    started.current ??= Date.now();
    fetch(questionsUrl(scope), { signal: controller.signal })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((data: QuestionList) => {
        setList(data);
        setFailed(false);
        if (data.status === "generating" && Date.now() - (started.current ?? 0) < POLL_LIMIT_MS) {
          timer = window.setTimeout(() => setTick((t) => t + 1), POLL_MS);
        }
      })
      .catch((e: Error) => {
        if (e.name !== "AbortError") setFailed(true);
      });
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [scope, tick]);

  return { list, failed, reload };
}

function TableQuestions({ scope, onPick, onOpenThread, canEdit }: { scope: Scope } & Handlers) {
  const [table, setTable] = useState<TableInfo | null>(null);
  const name = scope.path[scope.path.length - 1];
  const data = useDataQuestions(scope);
  const [editing, setEditing] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    const q = scope.path.map((p) => `path=${encodeURIComponent(p)}`).join("&");
    fetch(`/api/odn/connections/${encodeURIComponent(scope.connectionId)}/schema?${q}`, { signal: controller.signal })
      .then((r) => (r.ok ? r.json() : null))
      .then((t: TableInfo | null) => setTable(t))
      .catch(() => undefined);
    return () => controller.abort();
  }, [scope.connectionId, scope.path]);

  const starters = starterQuestions(name, table?.kind ?? null);
  const meta = table && [
    table.kind === "view" ? "View" : "Table",
    table.row_count ? `${table.row_count.toLocaleString()} rows` : null,
    `${table.columns.length} fields`,
    table.modified_at ? `modified ${relative(table.modified_at)}` : null,
  ].filter(Boolean).join(" · ");

  return (
    <section className="tq" aria-label={`Suggested questions for ${name}`}>
      <p className="tq-title">
        Ask about <code title={scope.path.join(".")}>{name}</code>
      </p>
      {meta && <p className="tq-meta">{meta}</p>}
      <h3 className="tq-group">Get to know this table</h3>
      <ul className="tq-chips">
        {starters.map((s) => (
          <li key={s.id}>
            <button type="button" className="tq-chip" onClick={() => onPick(s.question)} title={s.question}>
              {s.title}
            </button>
          </li>
        ))}
      </ul>
      <DataQuestions
        scope={scope} name={name} {...data} canEdit={canEdit} editing={editing} setEditing={setEditing}
        onPick={onPick} onOpenThread={onOpenThread}
      />
      <p className="tq-hint">
        Picks fill in the message box; press Enter to ask. <span aria-hidden="true">⚡</span> opens a saved answer
        instantly, with no tokens or data scan.
      </p>
    </section>
  );
}

function DataQuestions({
  scope, name, list, failed, reload, canEdit, editing, setEditing, onPick, onOpenThread,
}: {
  scope: Scope; name: string; list: QuestionList | null; failed: boolean; reload: () => void;
  editing: boolean; setEditing: (on: boolean) => void;
} & Handlers) {
  const [opening, setOpening] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function openSaved(q: DataQuestion) {
    setOpening(q.id);
    setError(null);
    try {
      const r = await fetch(
        `/api/odn/connections/${encodeURIComponent(scope.connectionId)}/questions/${q.id}/open`, { method: "POST" },
      );
      if (!r.ok) throw new Error(await detail(r));
      onOpenThread(((await r.json()) as { thread_id: string }).thread_id);
    } catch (e) {
      setError((e as Error).message);
      setOpening(null);
    }
  }

  if (failed && !list) return null;
  const generating = !list || list.status === "generating";
  const questions = list?.questions ?? [];
  if (!generating && questions.length === 0 && !canEdit) return null;

  return (
    <div className="tq-data">
      <div className="tq-group-row">
        <h3 className="tq-group">Questions about the data</h3>
        {canEdit && list && (
          <button type="button" className="tq-link" aria-pressed={editing} onClick={() => setEditing(!editing)}>
            {editing ? "Done" : "Edit"}
          </button>
        )}
      </div>
      {editing && list ? (
        <CurateQuestions scope={scope} name={name} list={list} reload={reload} />
      ) : generating && questions.length === 0 ? (
        <ul className="tq-list" aria-busy="true" aria-label="Writing suggested questions">
          {[0, 1, 2].map((i) => <li key={i} className="tq-skeleton" />)}
        </ul>
      ) : questions.length === 0 ? (
        <p className="tq-empty">No questions yet. Questions people ask about {name} will show up here.</p>
      ) : (
        <ul className="tq-list">
          {questions.map((q) => (
            <li key={q.id} className="tq-item">
              <button
                type="button" className="tq-q" title={q.question} disabled={opening !== null}
                onClick={() => (q.answer ? openSaved(q) : onPick(q.question))}
              >
                <QuestionText text={q.question} />
                <span className="tq-q-meta">
                  {q.answer && (
                    <span className={`tq-badge tq-badge--saved${q.answer.fresh ? "" : " is-stale"}`}>
                      <span aria-hidden="true">⚡</span>
                      {opening === q.id ? "Opening…" : q.answer.fresh ? "Saved answer" : "Saved answer · data changed"}
                    </span>
                  )}
                  <Badge q={q} />
                </span>
              </button>
              {q.answer && (
                <button
                  type="button" className="tq-again" aria-label={`Ask again for a fresh answer: ${q.question}`}
                  title="Ask again for a fresh answer" onClick={() => onPick(q.question)}
                >
                  ↻
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {error && <p className="tq-error" role="alert">{error}</p>}
    </div>
  );
}

/** Shows `column` spans in a question as code. */
function QuestionText({ text }: { text: string }) {
  return (
    <span className="tq-q-text">
      {text.split(/`([^`]+)`/).map((part, i) => (i % 2 ? <code key={i}>{part}</code> : part))}
    </span>
  );
}

function Badge({ q }: { q: DataQuestion }) {
  if (q.private) return <span className="tq-badge" title="Only you can see this question">Private</span>;
  if (q.source === "curated") return <span className="tq-badge tq-badge--accent">★ Pinned</span>;
  if (q.popular) return <span className="tq-badge tq-badge--accent">★ Popular</span>;
  if (q.asked_by_you) return <span className="tq-badge">You asked</span>;
  if (q.source === "asked") return <span className="tq-badge">Asked by a teammate</span>;
  return <span className="tq-badge">✦ Suggested</span>;
}

/** Data leads: hide or pin questions, add their own, or rewrite the suggested set. */
function CurateQuestions({ scope, name, list, reload }: { scope: Scope; name: string; list: QuestionList; reload: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [question, setQuestion] = useState("");
  const all = list.all ?? list.questions;

  async function run(action: () => Promise<Response>) {
    setBusy(true);
    setError(null);
    try {
      const r = await action();
      if (!r.ok) throw new Error(await detail(r));
      reload();
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    } finally {
      setBusy(false);
    }
  }
  const base = `/api/odn/connections/${encodeURIComponent(scope.connectionId)}/questions`;
  const patch = (q: DataQuestion, body: object) =>
    run(() => fetch(`${base}/${q.id}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    }));

  async function add(e: FormEvent) {
    e.preventDefault();
    const ok = await run(() => fetch(base, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: scope.path, title: title.trim(), question: question.trim() }),
    }));
    if (ok) {
      setTitle("");
      setQuestion("");
    }
  }

  return (
    <div className="tq-curate">
      <ul className="tq-list">
        {all.map((q) => (
          <li key={q.id} className={`tq-item tq-item--edit${q.hidden ? " is-hidden" : ""}`}>
            <QuestionText text={q.question} />
            <Badge q={q} />
            <span className="tq-edit-actions">
              <button type="button" className="tq-link" disabled={busy} onClick={() => patch(q, { pinned: q.source !== "curated" })}>
                {q.source === "curated" ? "Unpin" : "Pin"}
              </button>
              <button type="button" className="tq-link" disabled={busy} onClick={() => patch(q, { hidden: !q.hidden })}>
                {q.hidden ? "Show" : "Hide"}
              </button>
            </span>
          </li>
        ))}
      </ul>
      <form className="tq-add" onSubmit={add}>
        <input
          aria-label="Short title" placeholder="Short title" maxLength={60} required value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
        <input
          aria-label="Question" placeholder={`A question about ${name}`} minLength={12} maxLength={300} required
          value={question} onChange={(e) => setQuestion(e.target.value)}
        />
        <button type="submit" className="tq-chip" disabled={busy}>Add</button>
      </form>
      <button
        type="button" className="tq-link" disabled={busy || list.status === "generating"}
        onClick={() => run(() => fetch(questionsUrl(scope, "/regenerate"), { method: "POST" }))}
      >
        {list.status === "generating" ? "Writing new suggestions…" : "Write new suggestions"}
      </button>
      {error && <p className="tq-error" role="alert">{error}</p>}
    </div>
  );
}
