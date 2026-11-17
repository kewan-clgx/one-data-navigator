"use client";

import { useCoAgent } from "@copilotkit/react-core";

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

/** The latest chart and query result the agent produced in this conversation. */
export default function ResultsPanel() {
  const { last_result: result, last_chart: chart } = useAgentState();

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
          <h2>Latest result</h2>
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
