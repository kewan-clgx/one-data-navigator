"use client";

import { Markdown } from "@copilotkit/react-ui";
import { useCallback, useEffect, useId, useRef, useState, type FormEvent } from "react";

import { api } from "@/lib/client-api";
import { cleanAnswer, suggestTitle, summaryText } from "@/lib/share";

import Chart, { type ChartData } from "./Chart";

type Turn = {
  id: string; question: string; answer: string;
  tables: { name: string; row_count: number; columns: string[] }[];
  charts: { name: string; title: string; result: ChartData }[];
  default_artifacts: string[]; used_collibra: boolean; used_personal_connections: boolean;
};
type Preview = { title: string; turns: Turn[]; sharing_enabled: boolean; public_links: boolean };
type Audience = "public" | "organization";
type Created = { id: string; title: string; url: string; expires_at: string };
type MyShare = { id: string; title: string; audience: string; status: string; expires_at: string; views: number };

export const SHARES_CHANGED = "odn:shares-changed";

/** Microsoft Teams' "Share to Teams" window: pick any person, chat or channel; Teams shows the link preview. */
function teamsShareUrl(link: string, text: string) {
  return `https://teams.microsoft.com/share?href=${encodeURIComponent(link)}&msgText=${encodeURIComponent(text)}&preview=true`;
}

function mailtoUrl(subject: string, body: string) {
  return `mailto:?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
}

/** A starting title: the chart's title, the answer's first heading, or the tidied question. */
function suggested(t: Turn) {
  return suggestTitle(t.question, t.answer, t.charts.find((c) => t.default_artifacts.includes(c.name))?.title);
}

function ShareResult({ created, turn, audience, onDone }: { created: Created; turn: Turn; audience: Audience; onDone: () => void }) {
  const [copied, setCopied] = useState<string | null>(null);
  const summary = summaryText(created.title, turn.answer, created.url);
  const slackText = summaryText(created.title, turn.answer, created.url, "*");
  const copy = async (text: string, what: string) => {
    await navigator.clipboard.writeText(text);
    setCopied(what);
  };
  const slack = async () => {
    await copy(slackText, "slack");
    window.location.href = "slack://open"; // Slack has no pre-filled share link: paste into any chat or channel
  };
  return (
    <div className="share-result">
      <p>
        <strong>Link ready.</strong>{" "}
        {audience === "public"
          ? "Anyone with the link can open this summary, no sign-in needed; Teams and Slack show it as a preview card."
          : "People in your organization can open it after signing in."}{" "}
        It works until {new Date(created.expires_at).toLocaleDateString()}.
      </p>
      <div className="share-link">
        <input readOnly value={created.url} aria-label="Share link" onFocus={(e) => e.currentTarget.select()} />
        <button type="button" onClick={() => copy(created.url, "link")}>{copied === "link" ? "Copied ✓" : "Copy link"}</button>
      </div>
      <div className="share-apps" role="group" aria-label="Share via">
        <a className="share-app" href={teamsShareUrl(created.url, created.title)} target="_blank" rel="noreferrer">
          <span aria-hidden="true" className="share-app-icon share-app-icon--teams">T</span>Teams
        </a>
        <button type="button" className="share-app" onClick={slack}>
          <span aria-hidden="true" className="share-app-icon share-app-icon--slack">#</span>{copied === "slack" ? "Copied — paste in Slack" : "Slack"}
        </button>
        <a className="share-app" href={mailtoUrl(created.title, summary)}>
          <span aria-hidden="true" className="share-app-icon">✉</span>Email
        </a>
        <button type="button" className="share-app" onClick={() => copy(summary, "summary")} title="Title, one-line summary and link as text">
          <span aria-hidden="true" className="share-app-icon">❐</span>{copied === "summary" ? "Copied ✓" : "Copy summary"}
        </button>
      </div>
      <div className="settings-actions"><button type="button" onClick={onDone}>Done</button></div>
    </div>
  );
}

function MyShares({ threadId }: { threadId: string }) {
  const [items, setItems] = useState<MyShare[] | null>(null);
  const load = useCallback(() => {
    api<MyShare[]>(`shares?scope=mine&thread_id=${encodeURIComponent(threadId)}`).then(setItems).catch(() => setItems([]));
  }, [threadId]);
  useEffect(() => {
    load();
    window.addEventListener(SHARES_CHANGED, load);
    return () => window.removeEventListener(SHARES_CHANGED, load);
  }, [load]);
  if (!items?.length) return null;
  const act = async (path: string, init: RequestInit) => {
    await api(path, init).catch(() => {});
    load();
  };
  return (
    <div className="share-mine">
      <h3>Links for this conversation</h3>
      <ul className="connection-list">
        {items.map((s) => (
          <li key={s.id}>
            <a href={`/s/${s.id}`} target="_blank" rel="noreferrer">{s.title}</a>{" "}
            <span className="muted">
              {s.audience === "public" ? "anyone with the link" : s.audience === "organization" ? "organization" : "chosen people"} · {s.views} signed-in views ·{" "}
              {s.status === "active" ? `until ${new Date(s.expires_at).toLocaleDateString()}` : s.status}
            </span>{" "}
            {s.status !== "revoked" && (
              <>
                <button type="button" className="link" onClick={() => act(`shares/${s.id}/extend`, { method: "POST", body: JSON.stringify({ days: 30 }) })}>
                  Extend 30 days
                </button>{" "}
                <button type="button" className="link danger" onClick={() => window.confirm("Revoke this link? People can no longer open it.") && act(`shares/${s.id}`, { method: "DELETE" })}>
                  Revoke
                </button>
              </>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Share one answer: the question, its final answer and the chart/table it produced, as a link for Teams, Slack or email. */
export default function ShareDialog({ threadId, open, onClose }: { threadId: string; open: boolean; onClose: () => void }) {
  const ids = useId();
  const ref = useRef<HTMLDialogElement>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [audience, setAudience] = useState<Audience>("organization");
  const [days, setDays] = useState(30);
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState<Created | null>(null);
  const [turnId, setTurnId] = useState("");
  const [chosen, setChosen] = useState<string[]>([]);
  const [title, setTitle] = useState("");
  const turn = preview?.turns.find((t) => t.id === turnId) ?? null;

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    setPreview(null); setError(null); setCreated(null); setDays(30);
    api<Preview>(`shares/preview?thread_id=${encodeURIComponent(threadId)}`)
      .then((p) => {
        setPreview(p);
        const last = p.turns[p.turns.length - 1];
        setTurnId(last?.id ?? "");
        setChosen(last?.default_artifacts ?? []);
        if (last) setTitle(suggested(last));
        setAudience(p.public_links ? "public" : "organization");
      })
      .catch((e: Error) => setError(e.message));
  }, [open, threadId]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!turn) return;
    setBusy(true);
    setError(null);
    try {
      const share = await api<Created>("shares", {
        method: "POST",
        body: JSON.stringify({
          thread_id: threadId, title: title.trim() || turn.question.slice(0, 200), audience, include_results: chosen.length > 0,
          expires_days: days, mode: "answer", turn_id: turn.id, artifact_names: chosen,
        }),
      });
      setCreated(share);
      window.dispatchEvent(new Event(SHARES_CHANGED));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const pickTurn = (id: string) => {
    const t = preview?.turns.find((x) => x.id === id);
    setTurnId(id);
    setChosen(t?.default_artifacts ?? []);
    if (t) setTitle(suggested(t));
  };
  const toggle = (name: string, on: boolean) => setChosen((x) => (on ? [...x, name] : x.filter((n) => n !== name)));
  const tableLabel = (t: Turn["tables"][number]) =>
    `${t.row_count.toLocaleString()} ${t.row_count === 1 ? "row" : "rows"}${t.columns.length ? ` — ${t.columns.slice(0, 4).join(", ")}${t.columns.length > 4 ? "…" : ""}` : ""}`;
  const warnPersonal = !!turn?.used_personal_connections && chosen.some((n) => n.startsWith("q"));
  const warnCollibra = !!turn?.used_collibra;

  return (
    <dialog ref={ref} className="share-dialog" aria-labelledby={`${ids}-h`} onClose={onClose}>
      <div className="share-head">
        <h2 id={`${ids}-h`}>Share this answer</h2>
        <button type="button" className="link" aria-label="Close" onClick={onClose}>✕</button>
      </div>
      {created && turn ? (
        <ShareResult created={created} turn={turn} audience={audience} onDone={onClose} />
      ) : !preview ? (
        error ? <p className="error" role="alert">{error}</p> : <p className="muted">Loading…</p>
      ) : !preview.sharing_enabled ? (
        <p className="muted">Sharing is turned off for your organization. Ask an admin to turn it on.</p>
      ) : !turn ? (
        <p className="muted">There&apos;s no answer to share yet. Ask a question first.</p>
      ) : (
        <form className="settings-form" onSubmit={submit}>
          {preview.turns.length > 1 && (
            <label>
              Question
              <select value={turnId} onChange={(e) => pickTurn(e.target.value)}>
                {preview.turns.map((t) => (
                  <option key={t.id} value={t.id}>{t.question.length > 90 ? `${t.question.slice(0, 90)}…` : t.question}</option>
                ))}
              </select>
            </label>
          )}
          <label className="share-title">
            Title <span className="muted">— the headline people see in Teams, Slack and email. Make it short and clear.</span>
            <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={120} required
                   placeholder="e.g. Gold-tier businesses: photos and publishing by city" />
          </label>
          <article className="share-card" aria-label="What people will see">
            <p className="share-card-q">{title || turn.question}</p>
            <p className="muted share-card-asked">Question: {turn.question}</p>
            <div className="share-card-a"><Markdown content={cleanAnswer(turn.answer)} /></div>
            {turn.charts.map((c) =>
              chosen.includes(c.name) ? (
                <div key={c.name} className="share-visual">
                  <Chart data={c.result} compact />
                  <button type="button" className="link" onClick={() => toggle(c.name, false)}>Remove chart</button>
                </div>
              ) : (
                <button key={c.name} type="button" className="link share-add" onClick={() => toggle(c.name, true)}>
                  + Add chart: {c.title || "chart"}
                </button>
              ),
            )}
            {turn.tables.filter((t) => chosen.includes(t.name)).map((t) => (
              <div key={t.name} className="share-visual share-visual--table">
                <span>Table · {tableLabel(t)}</span>
                <button type="button" className="link" onClick={() => toggle(t.name, false)}>Remove</button>
              </div>
            ))}
            {turn.tables.some((t) => !chosen.includes(t.name)) && (
              <details className="share-more">
                <summary>Add a table from this answer ({turn.tables.filter((t) => !chosen.includes(t.name)).length})</summary>
                {turn.tables.filter((t) => !chosen.includes(t.name)).map((t) => (
                  <button key={t.name} type="button" className="link share-add" onClick={() => toggle(t.name, true)}>
                    + {tableLabel(t)}
                  </button>
                ))}
              </details>
            )}
          </article>
          {(warnPersonal || warnCollibra) && (
            <p className="share-warning" role="note">
              ⚠ This answer used{" "}
              {[warnPersonal && "your personal data connection", warnCollibra && "Collibra with your permissions"].filter(Boolean).join(" and ")}.
              {" "}People who open the link will see these results even if they can&apos;t access that data themselves.
            </p>
          )}
          <p className="muted">
            {preview.public_links
              ? "Anyone with the link can open this summary without signing in. Asking a follow-up needs a QueryMuse sign-in."
              : "Your organization requires people to sign in to open shared links."}
          </p>
          <label>
            Link expires after
            <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
              <option value={7}>7 days</option>
              <option value={30}>30 days</option>
              <option value={90}>90 days</option>
            </select>
          </label>
          <div className="settings-actions">
            <button type="submit" className="odn-btn" disabled={busy || !title.trim()}>{busy ? "Creating…" : "Create link"}</button>
            <button type="button" onClick={onClose}>Cancel</button>
          </div>
          {error && <p className="error" role="alert">{error}</p>}
        </form>
      )}
      {open && <MyShares threadId={threadId} />}
    </dialog>
  );
}
