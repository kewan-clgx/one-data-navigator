"use client";

import { useCallback, useEffect, useId, useRef, useState, type FormEvent } from "react";

import { api } from "@/lib/client-api";

type Person = { id: string; email: string; display_name: string | null };
type Preview = {
  title: string; messages: number; tables: number; charts: number; files: number;
  used_personal_connections: boolean; used_collibra: boolean; sharing_enabled: boolean;
};
type Created = { id: string; title: string; url: string; expires_at: string; emailed: number; recipients: Person[] };
type MyShare = {
  id: string; title: string; audience: string; status: string; expires_at: string; views: number;
  recipients: { id: string; email: string; name: string | null }[];
};

export const SHARES_CHANGED = "odn:shares-changed";

const label = (p: { display_name?: string | null; name?: string | null; email: string }) => p.display_name || p.name || p.email;

/** Microsoft Teams: opens a chat with exactly these people, message pre-filled (no Teams app needed). */
function teamsChatUrl(emails: string[], message: string) {
  return `https://teams.microsoft.com/l/chat/0/0?users=${emails.map(encodeURIComponent).join(",")}&message=${encodeURIComponent(message)}`;
}

/** Microsoft Teams' "Share to Teams" page: pick any person, chat or channel. */
function teamsShareUrl(link: string, title: string) {
  return `https://teams.microsoft.com/share?href=${encodeURIComponent(link)}&msgText=${encodeURIComponent(title)}&preview=true`;
}

function PeoplePicker({ value, onChange }: { value: Person[]; onChange: (p: Person[]) => void }) {
  const ids = useId();
  const [q, setQ] = useState("");
  const [options, setOptions] = useState<Person[]>([]);
  useEffect(() => {
    if (q.trim().length < 2) return setOptions([]);
    const t = window.setTimeout(() => {
      api<Person[]>(`shares/people?q=${encodeURIComponent(q.trim())}`)
        .then((r) => setOptions(r.filter((p) => !value.some((v) => v.id === p.id))))
        .catch(() => setOptions([]));
    }, 200);
    return () => window.clearTimeout(t);
  }, [q, value]);

  return (
    <div className="share-people">
      <label htmlFor={`${ids}-q`}>People in your organization</label>
      {value.length > 0 && (
        <ul className="share-chips" aria-label="Chosen people">
          {value.map((p) => (
            <li key={p.id}>
              {label(p)}{" "}
              <button type="button" aria-label={`Remove ${label(p)}`} onClick={() => onChange(value.filter((v) => v.id !== p.id))}>✕</button>
            </li>
          ))}
        </ul>
      )}
      <input
        id={`${ids}-q`}
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="Type a name or email"
        autoComplete="off"
        role="combobox"
        aria-expanded={options.length > 0}
        aria-controls={`${ids}-list`}
      />
      {options.length > 0 && (
        <ul id={`${ids}-list`} role="listbox" className="share-options">
          {options.map((p) => (
            <li key={p.id} role="option" aria-selected={false}>
              <button type="button" onClick={() => { onChange([...value, p]); setQ(""); setOptions([]); }}>
                <strong>{p.display_name || p.email}</strong> {p.display_name && <span className="muted">{p.email}</span>}
              </button>
            </li>
          ))}
        </ul>
      )}
      {q.trim().length >= 2 && options.length === 0 && (
        <p className="muted">No match. Only people who already use QueryMuse in your organization can be picked.</p>
      )}
    </div>
  );
}

function ShareResult({ created, onDone }: { created: Created; onDone: () => void }) {
  const [copied, setCopied] = useState<string | null>(null);
  const message = `${created.title} — ${created.url}`;
  const copy = async (text: string, what: string) => {
    await navigator.clipboard.writeText(text);
    setCopied(what);
  };
  const canShare = typeof navigator !== "undefined" && typeof navigator.share === "function";
  const emails = created.recipients.map((r) => r.email);
  return (
    <div className="share-result">
      <p>
        <strong>Shared.</strong>{" "}
        {created.recipients.length
          ? `${created.recipients.map(label).join(", ")} can open it${created.emailed ? " and got an email" : ""}.`
          : "Everyone in your organization can open it."}{" "}
        The link works until {new Date(created.expires_at).toLocaleDateString()}.
      </p>
      <div className="share-link">
        <input readOnly value={created.url} aria-label="Share link" onFocus={(e) => e.currentTarget.select()} />
        <button type="button" onClick={() => copy(created.url, "link")}>{copied === "link" ? "Copied ✓" : "Copy link"}</button>
      </div>
      <div className="settings-actions">
        {emails.length > 0 && (
          <a className="odn-btn odn-btn--small" href={teamsChatUrl(emails, message)} target="_blank" rel="noreferrer">Send in Teams</a>
        )}
        <a className="odn-btn odn-btn--outline odn-btn--small" href={teamsShareUrl(created.url, created.title)} target="_blank" rel="noreferrer">
          Share to Teams…
        </a>
        <button type="button" className="odn-btn odn-btn--outline odn-btn--small" onClick={() => copy(message, "slack")}>
          {copied === "slack" ? "Copied ✓" : "Copy for Slack"}
        </button>
        {canShare && (
          <button type="button" className="odn-btn odn-btn--outline odn-btn--small"
                  onClick={() => navigator.share({ title: created.title, url: created.url }).catch(() => {})}>
            Share…
          </button>
        )}
      </div>
      <p className="muted">Link previews in Slack/Teams show only “sign in to view”, never the content.</p>
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
              {s.audience === "organization" ? "everyone" : s.recipients.map((r) => r.name || r.email).join(", ")} · {s.views} viewed ·{" "}
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

/** Share a conversation with teammates: pick people (or everyone), choose what to include, then send/copy the link. */
export default function ShareDialog({ threadId, open, onClose }: { threadId: string; open: boolean; onClose: () => void }) {
  const ids = useId();
  const ref = useRef<HTMLDialogElement>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [audience, setAudience] = useState<"people" | "organization">("people");
  const [people, setPeople] = useState<Person[]>([]);
  const [includeResults, setIncludeResults] = useState(true);
  const [days, setDays] = useState(30);
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState<Created | null>(null);

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    setPreview(null); setError(null); setCreated(null); setPeople([]); setAudience("people"); setIncludeResults(true); setDays(30);
    api<Preview>(`shares/preview?thread_id=${encodeURIComponent(threadId)}`)
      .then((p) => { setPreview(p); setTitle(p.title); })
      .catch((e: Error) => setError(e.message));
  }, [open, threadId]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const share = await api<Created>("shares", {
        method: "POST",
        body: JSON.stringify({
          thread_id: threadId, title, audience, recipient_ids: audience === "people" ? people.map((p) => p.id) : [],
          include_results: includeResults, expires_days: days,
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

  const hasResults = preview && preview.tables + preview.charts + preview.files > 0;

  return (
    <dialog ref={ref} className="share-dialog" aria-labelledby={`${ids}-h`} onClose={onClose}>
      <div className="share-head">
        <h2 id={`${ids}-h`}>Share conversation</h2>
        <button type="button" className="link" aria-label="Close" onClick={onClose}>✕</button>
      </div>
      {created ? (
        <ShareResult created={created} onDone={onClose} />
      ) : !preview ? (
        error ? <p className="error" role="alert">{error}</p> : <p className="muted">Loading…</p>
      ) : !preview.sharing_enabled ? (
        <p className="muted">Sharing is turned off for your organization. Ask an admin to turn it on.</p>
      ) : (
        <form className="settings-form" onSubmit={submit}>
          <p className="muted">
            Teammates in your organization get a read-only copy of this conversation as it is now, and can continue it
            with their own follow-up questions. Your conversation isn&apos;t changed.
          </p>
          <label>
            Title
            <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} required />
          </label>
          <fieldset>
            <legend>Share with</legend>
            <label className="connection-share">
              <input type="radio" name={`${ids}-aud`} checked={audience === "people"} onChange={() => setAudience("people")} /> Specific people
            </label>
            <label className="connection-share">
              <input type="radio" name={`${ids}-aud`} checked={audience === "organization"} onChange={() => setAudience("organization")} /> Everyone in my organization
            </label>
          </fieldset>
          {audience === "people" && <PeoplePicker value={people} onChange={setPeople} />}
          <fieldset>
            <legend>Include</legend>
            <label className="connection-share">
              <input type="checkbox" checked={includeResults} onChange={(e) => setIncludeResults(e.target.checked)} /> Results
              {hasResults ? ` (${[preview.tables && `${preview.tables} tables`, preview.charts && `${preview.charts} charts`, preview.files && `${preview.files} files`].filter(Boolean).join(", ")})` : ""}
              {" "}— otherwise only questions, answers and SQL
            </label>
            {includeResults && (preview.used_personal_connections || preview.used_collibra) && (
              <p className="share-warning" role="note">
                ⚠ This conversation used{" "}
                {[preview.used_personal_connections && "your personal data connection", preview.used_collibra && "Collibra with your permissions"].filter(Boolean).join(" and ")}.
                {" "}People you share with will see these results even if they can&apos;t access that data themselves.
              </p>
            )}
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
            <button type="submit" disabled={busy || !title.trim() || (audience === "people" && people.length === 0)}>
              {busy ? "Sharing…" : "Share"}
            </button>
            <button type="button" onClick={onClose}>Cancel</button>
          </div>
          {error && <p className="error" role="alert">{error}</p>}
        </form>
      )}
      {open && <MyShares threadId={threadId} />}
    </dialog>
  );
}
