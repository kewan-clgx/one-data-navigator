"use client";

import { Markdown } from "@copilotkit/react-ui";
import { useEffect, useRef, useState } from "react";

import { ApiError, api } from "@/lib/client-api";
import { questionAndAnswer, type PublicShare } from "@/lib/share";

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
      window.location.assign("/");
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

/** The answer behind an "anyone with the link" share: question, answer, chart or first rows of the table. */
export default function PublicShareView({ share, signedIn }: { share: PublicShare; signedIn: boolean }) {
  const { question, answer } = questionAndAnswer(share.messages);
  return (
    <div className="shared-page">
      <header className="shared-head">
        <p className="shared-kicker">Shared answer · {share.organization}</p>
        <h1>{question || share.title}</h1>
        <p className="muted">
          Shared by {share.owner_name} on {new Date(share.created_at).toLocaleDateString()} · link works until{" "}
          {new Date(share.expires_at).toLocaleDateString()}
        </p>
      </header>
      {answer && <div className="shared-msg shared-msg--assistant"><Markdown content={answer} /></div>}
      {share.artifacts.map((a) =>
        a.kind === "chart" ? (
          <figure key={a.name} className="shared-artifact">
            <figcaption><strong>{a.title || "Chart"}</strong></figcaption>
            <Chart data={a.result} />
          </figure>
        ) : (
          <figure key={a.name} className="shared-artifact">
            <figcaption>
              <strong>{a.name}</strong> · {a.row_count.toLocaleString()} {a.row_count === 1 ? "row" : "rows"}
              {a.rows.length < a.row_count ? ` (first ${a.rows.length} shown)` : ""}
            </figcaption>
            <div className="org-table">
              <table className="grid">
                <thead><tr>{a.columns.map((c) => <th key={c} scope="col">{c}</th>)}</tr></thead>
                <tbody>
                  {a.rows.map((r, i) => (
                    <tr key={i}>{a.columns.map((c) => <td key={c}>{r[c] === null || r[c] === undefined ? "" : String(r[c])}</td>)}</tr>
                  ))}
                </tbody>
              </table>
            </div>
          </figure>
        ),
      )}
      <FollowUpButton shareId={share.id} signedIn={signedIn} organization={share.organization} />
    </div>
  );
}
