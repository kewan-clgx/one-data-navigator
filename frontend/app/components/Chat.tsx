"use client";

import { CopilotKit, useCoAgent, useHumanInTheLoop } from "@copilotkit/react-core";
import { CopilotChat } from "@copilotkit/react-ui";
import "@copilotkit/react-ui/styles.css";
import { useState } from "react";

import Chart, { type ChartData } from "./Chart";
import ResultsGrid from "./ResultsGrid";
import SplitPane from "./SplitPane";

const AGENT_ID = "odn";

type LastResult = {
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

type AgentState = { last_result?: LastResult; last_chart?: ChartData };

type ConfirmationArgs = {
  originalFunctionCall?: { name?: string; args?: Record<string, unknown> };
  toolConfirmation?: { hint?: string; payload?: unknown };
};

function cell(v: unknown): string {
  if (v === null || v === undefined) return "NULL";
  return typeof v === "object" ? JSON.stringify(v) : String(v);
}

function ConfirmationCard({
  args,
  status,
  respond,
}: {
  args: ConfirmationArgs;
  status: string;
  respond?: (value: unknown) => void;
}) {
  const [decision, setDecision] = useState<"approved" | "denied" | null>(null);
  const canRespond = status === "executing" && !!respond && !decision;

  function answer(confirmed: boolean) {
    if (!canRespond) return;
    setDecision(confirmed ? "approved" : "denied");
    // The tool re-checks that the approved payload matches the paused query.
    respond!(confirmed ? { confirmed: true, payload: args.toolConfirmation?.payload } : { confirmed: false });
  }

  return (
    <div role="group" aria-label="Query needs your approval" className="confirm">
      <strong>Approval needed</strong>
      {args.toolConfirmation?.hint && <pre className="hint">{args.toolConfirmation.hint}</pre>}
      {decision ? (
        <p role="status">{decision === "approved" ? "Approved — running." : "Declined — nothing was run."}</p>
      ) : canRespond ? (
        <p>
          <button type="button" onClick={() => answer(true)}>Run query</button>{" "}
          <button type="button" onClick={() => answer(false)}>Cancel</button>
        </p>
      ) : (
        <p className="muted">{status === "complete" ? "This request is no longer active." : "Preparing…"}</p>
      )}
    </div>
  );
}

function ChatPane() {
  const { state } = useCoAgent<AgentState>({ name: AGENT_ID, initialState: {} });
  useHumanInTheLoop({
    name: "adk_request_confirmation",
    description: "Ask the user to approve or deny an expensive query.",
    render: (props) => (
      <ConfirmationCard args={props.args as ConfirmationArgs} status={props.status} respond={props.respond} />
    ),
  });
  const result = state?.last_result;
  const chart = state?.last_chart;

  return (
    <SplitPane
      className="chat-layout"
      storageKey="odn.split.chat"
      defaultFraction={0.45}
      minLeft={340}
      minRight={320}
      label="Resize chat panel"
      left={
      <section className="chat" aria-label="Chat with the data analyst">
        <CopilotChat
          labels={{
            title: "Data analyst",
            initial: "Ask a question about your data, e.g. “How many businesses per status in CA?”",
          }}
        />
      </section>
      }
      right={
      <section className="chat-results" aria-label="Latest query results">
        {chart && <Chart data={chart} />}
        <h2>Latest results</h2>
        {!result ? (
          <p className="muted">Results of queries the analyst runs will appear here.</p>
        ) : (
          <>
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
      </section>
      }
    />
  );
}

export default function Chat() {
  return (
    <CopilotKit runtimeUrl="/api/copilotkit" agent={AGENT_ID}>
      <ChatPane />
    </CopilotKit>
  );
}
