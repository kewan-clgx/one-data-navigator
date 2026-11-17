"use client";

import { useCopilotAction, useHumanInTheLoop } from "@copilotkit/react-core";
import { createContext, useContext, useState } from "react";

import { setShowAgentSteps, useShowAgentSteps } from "@/lib/displayPrefs";

/** Lets chat cards ask the shell to reveal the Results tab. */
export const ShowResultsContext = createContext<() => void>(() => {});

const STATUS_LABEL: Record<string, string> = {
  inProgress: "Preparing",
  executing: "Running",
  complete: "Done",
};

// Tools whose successful result is drawn in the Results tab.
const RESULT_TOOLS = new Set(["run_sql", "query_workspace", "generate_chart"]);

type Json = Record<string, unknown>;
const isObject = (v: unknown): v is Json => !!v && typeof v === "object" && !Array.isArray(v);

function parse(value: unknown): unknown {
  if (typeof value !== "string") return value;
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}

function text(value: unknown): string {
  if (value === undefined || value === null) return "";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

type Outcome =
  | { kind: "ok"; summary: string | null }
  | { kind: "failed" | "blocked"; message: string }
  | { kind: "cancelled"; message: string }
  | { kind: "awaiting" };

function formatGb(bytes: unknown): string | null {
  return typeof bytes === "number" ? `${(bytes / 1024 ** 3).toFixed(2)} GB scanned` : null;
}

/** Maps the ODN agent tools' result shapes to a user-facing outcome. */
function outcomeOf(result: unknown): Outcome {
  if (!isObject(result)) return { kind: "ok", summary: null };
  const status = result.status;
  if (status === "awaiting_user_confirmation") return { kind: "awaiting" };
  if (status === "cancelled") return { kind: "cancelled", message: text(result.message) || "Nothing was run." };
  if (status === "rejected") return { kind: "blocked", message: text(result.reason) || "The query was not allowed." };
  if (status === "failed" || result.error !== undefined) {
    return { kind: "failed", message: text(result.error) || "The step failed." };
  }
  const parts: string[] = [];
  if (typeof result.workspace_table === "string") parts.push(`Saved as ${result.workspace_table}`);
  if (typeof result.row_count === "number") {
    parts.push(`${result.row_count.toLocaleString()} ${result.row_count === 1 ? "row" : "rows"}${result.truncated ? " (partial)" : ""}`);
  }
  const gb = formatGb(result.bytes_processed);
  if (gb) parts.push(gb);
  if (typeof result.points_plotted === "number") parts.push(`${result.points_plotted} points plotted`);
  if (Array.isArray(result.items)) parts.push(`${result.items.length} items`);
  if (Array.isArray(result.tables)) parts.push(`${result.tables.length} workspace tables`);
  return { kind: "ok", summary: parts.length ? parts.join(" · ") : null };
}

const TAG: Record<Outcome["kind"], string> = {
  ok: "",
  failed: "Failed",
  blocked: "Blocked",
  cancelled: "Cancelled",
  awaiting: "Awaiting approval",
};

function StepCard({ name, status, args, result }: { name: string; status: string; args: unknown; result: unknown }) {
  const showSteps = useShowAgentSteps();
  const showResults = useContext(ShowResultsContext);
  const complete = status === "complete";
  const outcome = complete ? outcomeOf(parse(result)) : null;
  const problem = outcome && (outcome.kind === "failed" || outcome.kind === "blocked") ? outcome : null;

  // Clean mode: only failures stay visible; results appear in the Results tab.
  if (!showSteps) {
    return problem ? (
      <div className="odn-step odn-step--failed">
        <div className="odn-step-error">
          <strong>{problem.kind === "blocked" ? "Query not allowed" : `${name} failed`}</strong>
          {problem.message}
        </div>
      </div>
    ) : null;
  }

  const sql = isObject(args) && typeof args.sql === "string" ? args.sql : null;
  const argsText = sql ?? (isObject(args) && Object.keys(args).length ? text(args) : "");
  const tag = outcome ? TAG[outcome.kind] || STATUS_LABEL.complete : STATUS_LABEL[status] ?? status;
  const tagClass = problem ? "odn-tag odn-tag--danger" : complete ? "odn-tag odn-tag--muted" : "odn-tag";

  return (
    <div className={`odn-step${problem ? " odn-step--failed" : ""}`}>
      <div className="odn-step-head">
        <span className="odn-step-name">
          <code>{name}</code>
        </span>
        <span className={tagClass}>{tag}</span>
      </div>
      {problem && <div className="odn-step-error">{problem.message}</div>}
      {outcome?.kind === "cancelled" && <div className="odn-step-summary">{outcome.message}</div>}
      {outcome?.kind === "ok" && outcome.summary && <div className="odn-step-summary">{outcome.summary}</div>}
      {outcome?.kind === "ok" && RESULT_TOOLS.has(name) && (
        <button type="button" className="odn-btn odn-btn--outline odn-btn--small" onClick={showResults}>
          View in Results
        </button>
      )}
      {argsText && (
        <pre tabIndex={0} aria-label={sql ? `${name} SQL` : `${name} arguments`}>
          {argsText}
        </pre>
      )}
      {complete && result !== undefined && (
        <details>
          <summary>Raw result</summary>
          <pre tabIndex={0} aria-label={`${name} raw result`}>
            {text(parse(result))}
          </pre>
        </details>
      )}
    </div>
  );
}

type ConfirmationArgs = {
  originalFunctionCall?: { name?: string; args?: Record<string, unknown> };
  toolConfirmation?: { hint?: string; payload?: unknown };
};

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
          <button type="button" className="odn-btn" onClick={() => answer(true)}>Run query</button>{" "}
          <button type="button" className="odn-btn odn-btn--outline" onClick={() => answer(false)}>Cancel</button>
        </p>
      ) : (
        <p className="muted">{status === "complete" ? "This request is no longer active." : "Preparing…"}</p>
      )}
    </div>
  );
}

/** Registers the chat renderers: approval prompts plus a card for every other agent tool call. */
export function useAgentStepRenderers() {
  useHumanInTheLoop({
    name: "adk_request_confirmation",
    description: "Ask the user to approve or deny an expensive query.",
    render: (props) => (
      <ConfirmationCard args={props.args as ConfirmationArgs} status={props.status} respond={props.respond} />
    ),
  });
  useCopilotAction({
    name: "*",
    render: ({ name, status, args, result }: { name: string; status: string; args: unknown; result: unknown }) => (
      <StepCard name={name} status={status} args={args} result={result} />
    ),
  });
}

export function AgentStepsToggle() {
  const on = useShowAgentSteps();
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      onClick={() => setShowAgentSteps(!on)}
      className="switch"
      title="Show or hide tool steps (Preparing / Running / Done). Errors and approvals always show."
    >
      <span aria-hidden className="switch-track">
        <span className="switch-thumb" />
      </span>
      Show agent steps
    </button>
  );
}
