"use client";

import { Markdown } from "@copilotkit/react-ui";
import { useCallback, useEffect, useId, useRef, useState, type FormEvent } from "react";

import { api } from "@/lib/client-api";
import { summaryText } from "@/lib/share";

type Turn = {
  id: string; question: string; answer: string;
  tables: { name: string; row_count: number }[]; charts: { name: string; title: string }[];
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

function ShareResult({ created, turn, audience, onDone }: { created: Created; turn: Turn; audience: Audience; onDone: () => void }) {
  const [copied, setCopied] = useState<string | null>(null);
  const summary = summaryText(turn.question, turn.answer, created.url);
  const copy = async (text: string, what: string) => {
    await navigator.clipboard.writeText(text);
    setCopied(what);
  };
  const slack = async () => {
    await copy(summary, "slack");
    window.location.href = "slack://open"; // Slack has no pre-filled share link: paste into any chat or channel
  };
  return (
    <div className="share-result">
      <p>
        <strong>Link ready.</strong>{" "}
        {audience === "public"
          ? "Anyone with the link can see this answer; Teams and Slack show it as a preview card."
          : "People in your organization can open it after signing in."}{" "}
        It works until {new Date(created.expires_at).toLocaleDateString()}.
      </p>
      <div className="share-link">
        <input readOnly value={created.url} aria-label="Share link" onFocus={(e) => e.currentTarget.select()} />
        <button type="button" onClick={() => copy(created.url, "link")}>{copied === "link" ? "Copied ✓" : "Copy link"}</button>
      </div>
      <div className="share-apps" role="group" aria-label="Share via">
        <a className="share-app" href={teamsShareUrl(created.url, turn.question)} target="_blank" rel="noreferrer">
          <span aria-hidden="true" className="share-app-icon share-app-icon--teams">T</span>Teams
        </a>
        <button type="button" className="share-app" onClick={slack}>
          <span aria-hidden="true" className="share-app-icon share-app-icon--slack">#</span>{copied === "slack" ? "Copied — paste in Slack" : "Slack"}
        </button>
        <a className="share-app" href={mailtoUrl(turn.question, summary)}>
          <span aria-hidden="true" className="share-app-icon">✉</span>Email
        </a>
        <button type="button" className="share-app" onClick={() => copy(summary, "summary")} title="Question, short answer and link as text">
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
          thread_id: threadId, title: turn.question.slice(0, 200), audience, include_results: chosen.length > 0,
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
    setTurnId(id);
    setChosen(preview?.turns.find((x) => x.id === id)?.default_artifacts ?? []);
  };
  const toggle = (name: string, on: boolean) => setChosen((x) => (on ? [...x, name] : x.filter((n) => n !== name)));
  const tableChoice = (t: Turn["tables"][number]) => (
    <label key={t.name} className="choice">
      <input type="checkbox" checked={chosen.includes(t.name)} onChange={(e) => toggle(t.name, e.target.checked)} />
      <span>Table {t.name} · {t.row_count.toLocaleString()} {t.row_count === 1 ? "row" : "rows"}</span>
    </label>
  );
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
          <article className="share-card" aria-label="What people will see">
            <p className="share-card-q">{turn.question}</p>
            <div className="share-card-a"><Markdown content={turn.answer} /></div>
            {turn.tables.length + turn.charts.length > 0 && (
              <div className="share-artifacts" role="group" aria-label="Include with the answer">
                {turn.charts.map((c) => (
                  <label key={c.name} className="choice">
                    <input type="checkbox" checked={chosen.includes(c.name)} onChange={(e) => toggle(c.name, e.target.checked)} />
                    <span>Chart{c.title ? `: ${c.title}` : ` ${c.name}`}</span>
                  </label>
                ))}
                {turn.tables.filter((t) => turn.default_artifacts.includes(t.name)).map((t) => tableChoice(t))}
                {turn.tables.some((t) => !turn.default_artifacts.includes(t.name)) && (
                  <details>
                    <summary>More results from this question ({turn.tables.filter((t) => !turn.default_artifacts.includes(t.name)).length})</summary>
                    {turn.tables.filter((t) => !turn.default_artifacts.includes(t.name)).map((t) => tableChoice(t))}
                  </details>
                )}
              </div>
            )}
          </article>
          {(warnPersonal || warnCollibra) && (
            <p className="share-warning" role="note">
              ⚠ This answer used{" "}
              {[warnPersonal && "your personal data connection", warnCollibra && "Collibra with your permissions"].filter(Boolean).join(" and ")}.
              {" "}People who open the link will see these results even if they can&apos;t access that data themselves.
            </p>
          )}
          <fieldset>
            <legend>Who can open the link</legend>
            {preview.public_links && (
              <label className="choice">
                <input type="radio" name={`${ids}-aud`} checked={audience === "public"} onChange={() => setAudience("public")} />
                <span>Anyone with the link <span className="muted">— Teams, Slack and email show the answer; follow-ups need a QueryMuse sign-in</span></span>
              </label>
            )}
            <label className="choice">
              <input type="radio" name={`${ids}-aud`} checked={audience === "organization"} onChange={() => setAudience("organization")} />
              <span>People in my organization <span className="muted">— they sign in to view; previews show no content</span></span>
            </label>
          </fieldset>
          <label>
            Link expires after
            <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
              <option value={7}>7 days</option>
              <option value={30}>30 days</option>
              <option value={90}>90 days</option>
            </select>
          </label>
          <div className="settings-actions">
            <button type="submit" className="odn-btn" disabled={busy}>{busy ? "Creating…" : "Create link"}</button>
            <button type="button" onClick={onClose}>Cancel</button>
          </div>
          {error && <p className="error" role="alert">{error}</p>}
        </form>
      )}
      {open && <MyShares threadId={threadId} />}
    </dialog>
  );
}
