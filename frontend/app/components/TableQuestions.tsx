"use client";

import { UseAgentUpdate, useAgent } from "@copilotkit/react-core/v2";
import { useEffect, useState } from "react";

import { AGENT_ID } from "./Results";

type Scope = { connectionId: string; path: string[] };
type TableInfo = { kind: "table" | "view"; row_count?: number | null; columns: unknown[]; modified_at?: string | null };
type Starter = { id: string; title: string; question: string };

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
 * Empty-conversation card for a scoped table: starter questions that pre-fill the chat input (never send), so the
 * user decides when to spend tokens.
 */
export default function ScopedStarters({ scope, onPick }: { scope: Scope | null; onPick: (question: string) => void }) {
  const empty = useConversationEmpty();
  return scope && empty ? <TableQuestions scope={scope} onPick={onPick} /> : null;
}

function TableQuestions({ scope, onPick }: { scope: Scope; onPick: (question: string) => void }) {
  const key = `${scope.connectionId}:${scope.path.join(".")}`;
  const [info, setInfo] = useState<{ key: string; table: TableInfo | null } | null>(null);
  const name = scope.path[scope.path.length - 1];

  useEffect(() => {
    const controller = new AbortController();
    const q = scope.path.map((p) => `path=${encodeURIComponent(p)}`).join("&");
    fetch(`/api/odn/connections/${encodeURIComponent(scope.connectionId)}/schema?${q}`, { signal: controller.signal })
      .then((r) => (r.ok ? r.json() : null))
      .then((table: TableInfo | null) => setInfo({ key, table }))
      .catch((e: Error) => {
        if (e.name !== "AbortError") setInfo({ key, table: null });
      });
    return () => controller.abort();
  }, [key, scope.connectionId, scope.path]);

  const table = info?.key === key ? info.table : null;
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
      <p className="tq-hint">Picks fill in the message box; press Enter to ask. Answered from the table&apos;s metadata, no data scan.</p>
    </section>
  );
}
