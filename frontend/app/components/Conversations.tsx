"use client";

import { useCallback, useEffect, useState } from "react";

import { api } from "@/lib/client-api";

import { SHARES_CHANGED } from "./ShareDialog";

type SharedWithMe = {
  id: string; title: string; owner_name: string | null; owner_email: string; created_at: string;
  first_viewed_at: string | null;
};

/** Conversations teammates shared with the signed-in user (newest first; "new" until opened). */
function SharedWithMeList() {
  const [items, setItems] = useState<SharedWithMe[]>([]);
  const load = useCallback(() => {
    api<SharedWithMe[]>("shares?scope=with_me").then(setItems).catch(() => setItems([]));
  }, []);
  useEffect(() => {
    load();
    window.addEventListener(SHARES_CHANGED, load);
    window.addEventListener("focus", load);
    return () => {
      window.removeEventListener(SHARES_CHANGED, load);
      window.removeEventListener("focus", load);
    };
  }, [load]);
  if (!items.length) return null;
  const unseen = items.filter((s) => !s.first_viewed_at).length;
  return (
    <section aria-label="Shared with me">
      <span className="odn-panel-heading">Shared with me{unseen ? ` · ${unseen} new` : ""}</span>
      <ul className="odn-chat-list">
        {items.map((s) => (
          <li key={s.id} className={`odn-chat-item${s.first_viewed_at ? "" : " odn-chat-item--active"}`}>
            <a className="odn-chat-open" href={`/s/${s.id}`}>
              <span className="odn-chat-title">{s.title}</span>
              <span className="odn-chat-meta">
                from {s.owner_name || s.owner_email} · {new Date(s.created_at).toLocaleDateString()}
                {s.first_viewed_at ? "" : " · new"}
              </span>
            </a>
          </li>
        ))}
      </ul>
    </section>
  );
}

export type Conversation = { threadId: string; sessionId: string; title: string; updatedAt: number; continuedFrom?: string | null };

/** The signed-in user's conversations; refetches whenever `revision` changes. */
export function useConversations(revision: number) {
  const [items, setItems] = useState<Conversation[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    fetch("/api/agent/conversations", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((d) => {
        setItems(Array.isArray(d.conversations) ? d.conversations : []);
        setError(null);
      })
      .catch(() => setError("Could not load your conversations."));
  }, []);

  useEffect(load, [load, revision]);
  const remove = useCallback((threadId: string) => {
    setItems((list) => list?.filter((c) => c.threadId !== threadId) ?? list);
  }, []);
  return { items, error, reload: load, remove };
}

export function conversationTitle(c: Conversation | undefined): string {
  if (!c) return "Conversation";
  return c.title || `Conversation from ${new Date(c.updatedAt * 1000).toLocaleDateString()}`;
}

function when(epochSeconds: number): string {
  const d = new Date(epochSeconds * 1000);
  const today = new Date();
  return d.toDateString() === today.toDateString()
    ? d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })
    : d.toLocaleDateString([], { month: "short", day: "numeric", year: d.getFullYear() === today.getFullYear() ? undefined : "numeric" });
}

/** Chats tab: reopen, start or delete conversations. */
export default function ChatsPanel({
  threadId,
  items,
  error,
  onOpen,
  onNew,
  onDeleted,
}: {
  threadId: string;
  items: Conversation[] | null;
  error: string | null;
  onOpen: (threadId: string) => void;
  onNew: () => void;
  onDeleted: (threadId: string) => void;
}) {
  const [confirming, setConfirming] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  async function remove(id: string) {
    setDeleteError(null);
    const res = await fetch(`/api/agent/conversations/${encodeURIComponent(id)}`, { method: "DELETE" });
    setConfirming(null);
    if (res.ok || res.status === 404) onDeleted(id);
    else setDeleteError("Could not delete the conversation. Try again.");
  }

  const current = items?.some((c) => c.threadId === threadId);

  return (
    <div className="odn-list-panel">
      <div className="odn-list-toolbar">
        <button type="button" className="odn-btn odn-btn--on-panel odn-btn--small" onClick={onNew}>
          + New conversation
        </button>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      {deleteError && <p className="error" role="alert">{deleteError}</p>}
      {items === null && !error && <p className="odn-panel-note">Loading…</p>}
      <SharedWithMeList />
      {items?.length === 0 && <p className="odn-panel-note">No saved conversations yet. Ask a question to start one.</p>}
      {items && items.length > 0 && !current && (
        <p className="odn-panel-note">You&apos;re in a new conversation; it appears here after your first message.</p>
      )}
      <ul className="odn-chat-list" aria-label="Your conversations">
        {items?.map((c) => {
          const active = c.threadId === threadId;
          return (
            <li key={c.threadId} className={`odn-chat-item${active ? " odn-chat-item--active" : ""}`}>
              <button
                type="button"
                className="odn-chat-open"
                aria-current={active ? "true" : undefined}
                onClick={() => onOpen(c.threadId)}
              >
                <span className="odn-chat-title">{conversationTitle(c)}</span>
                <span className="odn-chat-meta">
                  {when(c.updatedAt)}
                  {c.continuedFrom && ` · shared by ${c.continuedFrom}`}
                  {active && " · open"}
                </span>
              </button>
              {confirming === c.threadId ? (
                <span className="odn-chat-confirm" role="group" aria-label="Confirm delete">
                  <button type="button" className="odn-btn odn-btn--on-panel odn-btn--small" onClick={() => remove(c.threadId)}>
                    Delete
                  </button>
                  <button type="button" className="odn-btn odn-btn--ghost-panel odn-btn--small" onClick={() => setConfirming(null)}>
                    Keep
                  </button>
                </span>
              ) : (
                <button
                  type="button"
                  className="odn-chat-delete"
                  aria-label={`Delete conversation: ${conversationTitle(c)}`}
                  title="Delete conversation (its tables and charts stay in Artifacts)"
                  onClick={() => setConfirming(c.threadId)}
                >
                  ✕
                </button>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
