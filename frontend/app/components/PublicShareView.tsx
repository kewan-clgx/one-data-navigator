"use client";

import { Markdown } from "@copilotkit/react-ui";
import { useEffect, useRef, useState } from "react";

import { ApiError, api } from "@/lib/client-api";
import { questionAndAnswer, type PublicArtifact, type PublicShare } from "@/lib/share";

import Chart from "./Chart";

const THREAD_KEY = "odn.activeThreadId";

/** "Ask a follow-up": signs the reader in if needed, copies the answer into their own conversation and opens it. */
export function FollowUpButton({ shareId, signedIn, organization }: { shareId: string; signedIn: boolean; organization?: string }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false);

  async function go() {
    if (!signedIn) {
      document.cookie = `odn_return=${encodeURIComponent(`/s/${shareId}?followup=1`)}; Max-Age=900; Path=/; SameSite=Lax`;
      window.location.assign("/signin");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const { thread_id } = await api<{ thread_id: string }>(`shares/${shareId}/continue`, { method: "POST" });
      localStorage.setItem(THREAD_KEY, thread_id);
      window.location.assign("/");
    } catch (e) {
      setError(
        e instanceof ApiError && e.status === 404
          ? `Only people in ${organization ?? "the organization that shared this"} can ask follow-up questions on this answer.`
          : (e as Error).message,
      );
      setBusy(false);
    }
  }

  // Back from sign-in with ?followup=1: carry on where the reader left off.
  useEffect(() => {
    if (!signedIn || started.current) return;
    const url = new URL(window.location.href);
    if (url.searchParams.get("followup") !== "1") return;
    started.current = true;
    url.searchParams.delete("followup");
    window.history.replaceState(null, "", url.pathname);
    void go();
  }, [signedIn]);

  return (
    <div className="share-followup">
      <button type="button" className="odn-btn" onClick={go} disabled={busy}>
        {busy ? "Opening…" : "Ask a follow-up"}
      </button>
      <span className="muted">
        {signedIn ? "Opens your own copy of this conversation in QueryMuse." : "Sign in to QueryMuse to keep asking about this."}
      </span>
      {error && <p className="error" role="alert">{error}</p>}
    </div>
  );
}

type ReportProps = {
  shareId: string; title: string; question: string; answer: string; owner: string; organization?: string;
  createdAt: string; status: string; expiresAt: string; artifacts: PublicArtifact[]; signedIn: boolean;
};

/** A shared answer as a short report: the question, the answer (no SQL or result names), the chart or table. */
export function SummaryReport(p: ReportProps) {
  return (
    <div className="shared-page shared-report">
      <header className="shared-head">
        <p className="shared-kicker">Shared answer{p.organization ? ` · ${p.organization}` : ""}</p>
        <h1>{p.title}</h1>
        {p.question && p.question !== p.title && <p className="shared-question">“{p.question}”</p>}
        <p className="muted">
          Shared by {p.owner} on {new Date(p.createdAt).toLocaleDateString()} ·{" "}
          {p.status === "active" ? `link works until ${new Date(p.expiresAt).toLocaleDateString()}` : `link ${p.status}`}
        </p>
      </header>
      {p.answer && <div className="shared-msg shared-msg--assistant"><Markdown content={p.answer} /></div>}
      {p.artifacts.map((a, i) =>
        a.kind === "chart" ? (
          <div key={i} className="shared-artifact"><Chart data={a.result} compact /></div>
        ) : (
          <figure key={i} className="shared-artifact">
            <div className="org-table">
              <table className="grid">
                <thead><tr>{a.columns.map((c) => <th key={c} scope="col">{c}</th>)}</tr></thead>
                <tbody>
                  {a.rows.map((r, j) => (
                    <tr key={j}>{a.columns.map((c) => <td key={c}>{r[c] === null || r[c] === undefined ? "" : String(r[c])}</td>)}</tr>
                  ))}
                </tbody>
              </table>
            </div>
            {a.rows.length < a.row_count && (
              <figcaption className="muted">First {a.rows.length} of {a.row_count.toLocaleString()} rows</figcaption>
            )}
          </figure>
        ),
      )}
      {p.status === "active" && <FollowUpButton shareId={p.shareId} signedIn={p.signedIn} organization={p.organization} />}
    </div>
  );
}

/** The answer behind an "anyone with the link" share, as the API returns it without sign-in. */
export default function PublicShareView({ share, signedIn }: { share: PublicShare; signedIn: boolean }) {
  const { question, answer } = questionAndAnswer(share.messages);
  return (
    <SummaryReport
      shareId={share.id} title={share.title} question={question} answer={answer} owner={share.owner_name}
      organization={share.organization} createdAt={share.created_at} expiresAt={share.expires_at} status="active"
      artifacts={share.artifacts} signedIn={signedIn}
    />
  );
}
