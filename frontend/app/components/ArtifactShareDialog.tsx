"use client";

import { useEffect, useId, useRef, useState, type FormEvent } from "react";

import { api } from "@/lib/client-api";

import { MyShares, SHARES_CHANGED, ShareResult, type Audience, type Created } from "./ShareDialog";

export type ShareTarget = { kind: "table" | "chart" | "file" | "sql"; id: string; title: string };
type Options = { enabled: boolean; public_links: boolean; org_wall: boolean; public_wall: boolean; personal: boolean };

const KIND_LABEL = { table: "table", chart: "chart", file: "file", sql: "SQL" } as const;
// Signed-out readers only get summaries (the plot, the first rows), as with shared answers.
const PUBLIC_KINDS = new Set(["table", "chart"]);

/**
 * Share one artifact as a link (a frozen copy, like a shared answer): organization or "anyone with the link", 7/30/90
 * days, then Teams, Slack, email or copy. Earlier links for the same item are listed to extend or revoke.
 */
export default function ArtifactShareDialog({ target, onClose }: { target: ShareTarget; onClose: () => void }) {
  const ids = useId();
  const ref = useRef<HTMLDialogElement>(null);
  const [options, setOptions] = useState<Options | null>(null);
  const [title, setTitle] = useState(target.title);
  const [audience, setAudience] = useState<Audience>("organization");
  const [days, setDays] = useState(30);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<(Created & { description?: string }) | null>(null);
  const canPublic = !!options?.public_links && PUBLIC_KINDS.has(target.kind);

  useEffect(() => {
    ref.current?.showModal();
    api<Options>("shares/options").then(setOptions).catch((e: Error) => setError(e.message));
  }, []);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const share = await api<Created & { description?: string }>("shares/artifact", {
        method: "POST",
        body: JSON.stringify({ kind: target.kind, id: target.id, title: title.trim(), audience, expires_days: days }),
      });
      setCreated(share);
      window.dispatchEvent(new Event(SHARES_CHANGED));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <dialog ref={ref} className="share-dialog" aria-labelledby={`${ids}-h`} onClose={onClose}>
      <div className="share-head">
        <h2 id={`${ids}-h`}>Share this {KIND_LABEL[target.kind]}</h2>
        <button type="button" className="link" aria-label="Close" onClick={onClose}>✕</button>
      </div>
      {created ? (
        <ShareResult created={created} answer={created.description ?? ""} audience={audience} onDone={onClose} />
      ) : !options ? (
        error ? <p className="error" role="alert">{error}</p> : <p className="muted">Loading…</p>
      ) : !options.enabled ? (
        <p className="muted">Sharing is turned off for your organization. Ask an admin to turn it on.</p>
      ) : (
        <form className="settings-form" onSubmit={submit}>
          <label className="share-title">
            Title <span className="muted">— the headline people see in Teams, Slack and email.</span>
            <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={120} required />
          </label>
          <fieldset className="share-wall">
            <legend>Who can open the link</legend>
            <label className="choice">
              <input type="radio" name={`${ids}-aud`} checked={audience === "organization"} onChange={() => setAudience("organization")} />
              <span>People in your organization <span className="muted">— after signing in</span></span>
            </label>
            <label className="choice">
              <input type="radio" name={`${ids}-aud`} checked={audience === "public"} disabled={!canPublic}
                     onChange={() => setAudience("public")} />
              <span>
                Anyone with the link{" "}
                <span className="muted">
                  {!options.public_links
                    ? "— your organization requires sign-in"
                    : !PUBLIC_KINDS.has(target.kind)
                      ? "— SQL and files always need sign-in"
                      : target.kind === "table" ? "— shows the first rows, no sign-in" : "— shows the chart, no sign-in"}
                </span>
              </span>
            </label>
          </fieldset>
          <p className="muted">
            People get a copy as it is now: later changes, archiving or deleting it here don&apos;t change the link. Revoke
            the link to stop sharing.
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
      <MyShares threadId={`artifact:${target.kind}:${target.id}`} switches={null} heading="Links for this item" />
    </dialog>
  );
}
