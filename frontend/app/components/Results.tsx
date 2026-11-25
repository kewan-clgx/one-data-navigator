"use client";

import { useCoAgent } from "@copilotkit/react-core";
import { useContext, useEffect, useState } from "react";

import { ChatSessionContext } from "./AgentSteps";
import Chart, { type ChartData } from "./Chart";
import ResultsGrid from "./ResultsGrid";

export const AGENT_ID = "odn";

export type LastResult = {
  session_id?: string;
  name?: string;
  kind?: "warehouse" | "workspace";
  sql: string;
  explanation?: string;
  derived_from?: string[];
  source_tables?: string[];
  columns: string[];
  rows: Record<string, unknown>[];
  row_count: number;
  truncated: boolean;
  bytes_processed: number | null;
};

export type AgentState = { last_result?: LastResult; last_chart?: ChartData };

export function useAgentState(): AgentState {
  const { state } = useCoAgent<AgentState>({ name: AGENT_ID, initialState: {} });
  return state ?? {};
}

function cell(v: unknown): string {
  if (v === null || v === undefined) return "NULL";
  return typeof v === "object" ? JSON.stringify(v) : String(v);
}

type Artifact = {
  id: string;
  name: string;
  kind: string;
  parent_ids: string[];
  sql_text: string;
  source_tables: string[];
  row_count: number;
  truncated: boolean;
  columns: { name: string }[];
  explanation: string;
};

function asResult(a: Artifact, all: Artifact[], sessionId: string): LastResult {
  return {
    session_id: sessionId,
    name: a.name,
    kind: a.kind === "workspace" ? "workspace" : "warehouse",
    sql: a.sql_text,
    explanation: a.explanation,
    derived_from: a.parent_ids.map((id) => all.find((p) => p.id === id)?.name ?? "").filter(Boolean),
    source_tables: a.source_tables,
    columns: a.columns.map((c) => c.name),
    rows: [],
    row_count: a.row_count,
    truncated: a.truncated,
    bytes_processed: null,
  };
}

/** The conversation's query results (q1, q2, ...), oldest first, refreshed when a new result arrives. */
function useResultHistory(sessionId: string, latestName: string | undefined): Artifact[] {
  const [loaded, setLoaded] = useState<{ sessionId: string; items: Artifact[] }>({ sessionId: "", items: [] });
  useEffect(() => {
    if (!sessionId) return;
    let cancelled = false;
    fetch(`/api/odn/workspace/${encodeURIComponent(sessionId)}/artifacts`, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : []))
      .then((list: Artifact[]) => {
        if (cancelled || !Array.isArray(list)) return;
        setLoaded({ sessionId, items: list.filter((a) => a.kind === "warehouse" || a.kind === "workspace") });
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [sessionId, latestName]);
  return loaded.sessionId === sessionId ? loaded.items : [];
}

/** The latest chart and query result the agent produced in this conversation, with earlier results one click back. */
export default function ResultsPanel() {
  const { last_result: latest, last_chart: chart } = useAgentState();
  const chatSession = useContext(ChatSessionContext);
  const sessionId = latest?.session_id ?? chatSession;
  const history = useResultHistory(sessionId, latest?.name);
  // No pick follows the latest result; a pick pins an earlier one until a new result (or conversation) arrives.
  const followKey = `${sessionId}/${latest?.name ?? ""}`;
  const [pick, setPick] = useState<{ key: string; name: string } | null>(null);
  const picked = pick?.key === followKey ? pick.name : null;

  const latestIndex = latest?.name ? history.findIndex((a) => a.name === latest.name) : history.length - 1;
  const index = picked ? history.findIndex((a) => a.name === picked) : latestIndex;
  const pickedArtifact = picked && index >= 0 ? history[index] : null;
  const result = pickedArtifact ? asResult(pickedArtifact, history, sessionId) : latest ?? (history.length ? asResult(history[history.length - 1], history, sessionId) : undefined);
  const isLatest = !pickedArtifact || index === history.length - 1;
  const go = (i: number) => setPick(i === latestIndex ? null : { key: followKey, name: history[i].name });

  if (!result && !chart) {
    return (
      <p className="odn-empty">
        Charts and query results from the conversation appear here. Ask a question in the chat to get started.
      </p>
    );
  }

  return (
    <div className="odn-results">
      {chart && <Chart data={chart} />}
      {result && (
        <>
          <div className="result-nav">
            <h2>{isLatest ? "Latest result" : "Earlier result"}</h2>
            {history.length > 1 && index >= 0 && (
              <nav className="pager" aria-label="Results in this conversation">
                <button type="button" className="odn-btn odn-btn--outline odn-btn--small" onClick={() => go(index - 1)} disabled={index <= 0} aria-label="Previous result">
                  ‹ {index > 0 ? history[index - 1].name : "Prev"}
                </button>
                <span className="muted" aria-live="polite">
                  {index + 1} of {history.length}
                </span>
                <button type="button" className="odn-btn odn-btn--outline odn-btn--small" onClick={() => go(index + 1)} disabled={index >= history.length - 1} aria-label="Next result">
                  {index < history.length - 1 ? history[index + 1].name : "Next"} ›
                </button>
              </nav>
            )}
          </div>
          {result.name && (
            <h3 className="artifact-title">
              <code>{result.name}</code>{" "}
              <span className={`badge ${result.kind}`}>
                {result.kind === "workspace" ? "Workspace follow-up · no warehouse scan" : "Warehouse query"}
              </span>
            </h3>
          )}
          {result.explanation && <p className="explanation">{result.explanation}</p>}
          <p className="muted">
            {result.row_count.toLocaleString()} {result.row_count === 1 ? "row" : "rows"}
            {result.kind === "warehouse" && result.bytes_processed != null &&
              ` · scanned ${(result.bytes_processed / 1024 ** 3).toFixed(2)} GB`}
            {result.truncated && <strong> · partial result</strong>}
          </p>
          {(result.derived_from?.length || result.source_tables?.length) ? (
            <p className="muted lineage">
              {result.derived_from?.length ? <>Derived from <code>{result.derived_from.join(", ")}</code> · </> : null}
              Source: {result.source_tables?.length ? result.source_tables.join(", ") : "unknown"}
            </p>
          ) : null}
          <details>
            <summary>SQL</summary>
            <pre className="hint">{result.sql}</pre>
          </details>
          {result.session_id && result.name ? (
            <ResultsGrid
              sessionId={result.session_id}
              name={result.name}
              columns={result.columns}
              initialRows={result.rows}
              key={result.name}
            />
          ) : (
            <div className="results">
              <table className="grid">
                <thead>
                  <tr>
                    {result.columns.map((c) => (
                      <th key={c} scope="col">{c}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {result.rows.map((r, i) => (
                    <tr key={i}>
                      {result.columns.map((c) => (
                        <td key={c}>{cell(r[c])}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}
