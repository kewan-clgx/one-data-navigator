"use client";

import { useCallback, useEffect, useId, useState, type FormEvent } from "react";

import { ApiError, api } from "@/lib/client-api";
import { highlight } from "@/lib/share";
import {
  PAGE_SIZE, PUBLIC_WALL_NAME, WALL_SORTS, timeAgo,
  type WallCard, type WallPage, type WallScope, type WallSort,
} from "@/lib/wall";

import Chart from "./Chart";

function Card({ card, appUrl, signedIn, onVote, showOrganization }: {
  card: WallCard; appUrl: string; signedIn: boolean; showOrganization: boolean;
  onVote?: (card: WallCard) => void;
}) {
  const report = `${appUrl}/s/${card.id}`;
  const text = highlight(card.answer, 220);
  const by = [card.owner_name, showOrganization ? card.organization : null].filter(Boolean).join(" · ");
  const voteLabel = card.is_owner ? "Your insight" : card.my_vote ? "Remove your upvote" : "Upvote";
  return (
    <article className={`wall-card${card.featured ? " wall-card--featured" : ""}`}>
      <div className="wall-vote">
        {signedIn ? (
          <button type="button" className="wall-vote-btn" aria-pressed={!!card.my_vote} disabled={card.is_owner}
                  title={voteLabel} aria-label={`${voteLabel}: ${card.title}`} onClick={() => onVote?.(card)}>▲</button>
        ) : (
          <a className="wall-vote-btn" href={`${appUrl}/signin`} title="Sign in to upvote" aria-label="Sign in to upvote">▲</a>
        )}
        <span className="wall-votes" aria-label={`${card.votes} upvotes`}>{card.votes}</span>
      </div>
      <div className="wall-card-body">
        {card.featured && <span className="wall-badge">Featured</span>}
        <h2><a href={report}>{card.title}</a></h2>
        {card.question && card.question !== card.title && <p className="wall-question">“{card.question}”</p>}
        {text && <p className="wall-highlight">{text}</p>}
        {card.chart && <div className="wall-chart"><Chart data={card.chart.result} compact /></div>}
        <p className="muted wall-meta">
          {by && <>{by} · </>}{timeAgo(card.listed_at)}
          {card.follow_ups > 0 && <> · {card.follow_ups} follow-up{card.follow_ups === 1 ? "" : "s"}</>}
        </p>
      </div>
    </article>
  );
}

type Props = {
  appUrl: string;
  signedIn: boolean;
  organization?: string;
  /** Signed out: the server-rendered public wall (sort, search and paging use the page's query string). */
  initial?: { items: WallCard[]; sort: WallSort; q: string; offset: number };
};

/** Shared answers posted to the organization's wall or the public QueryMuse wall, ranked Hot / New / Top. */
export default function InsightWall({ appUrl, signedIn, organization, initial }: Props) {
  const ids = useId();
  const [scope, setScope] = useState<WallScope>("organization");
  const [sort, setSort] = useState<WallSort>(initial?.sort ?? "hot");
  const [q, setQ] = useState(initial?.q ?? "");
  const [query, setQuery] = useState(initial?.q ?? "");
  const [page, setPage] = useState<WallPage | null>(null);
  const [more, setMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (offset = 0) => {
    const params = new URLSearchParams({ scope, sort, offset: String(offset) });
    if (query) params.set("q", query);
    const next = await api<WallPage>(`wall?${params}`);
    // The personal tenant has no organization wall: show the public one.
    if (scope === "organization" && !next.enabled && next.settings.personal) {
      setScope("public");
      return;
    }
    setPage((prev) => (offset && prev ? { ...next, items: [...prev.items, ...next.items] } : next));
    setMore(next.items.length === PAGE_SIZE);
  }, [scope, sort, query]);

  useEffect(() => {
    if (!signedIn) return;
    setError(null);
    load().catch((e: Error) => setError(e.message));
  }, [signedIn, load]);

  async function vote(card: WallCard) {
    try {
      const r = await api<{ votes: number; my_vote: boolean }>(`wall/${card.id}/vote`, { method: card.my_vote ? "DELETE" : "POST" });
      setPage((p) => p && { ...p, items: p.items.map((c) => (c.id === card.id ? { ...c, votes: r.votes, my_vote: r.my_vote } : c)) });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't save your upvote.");
    }
  }

  function search(e: FormEvent) {
    e.preventDefault();
    setQuery(q.trim());
  }

  const items = signedIn ? page?.items ?? null : initial?.items ?? [];
  const personal = !!page?.settings.personal;
  const heading = scope === "public" || !signedIn ? PUBLIC_WALL_NAME : `${organization ?? "Your organization"} Insight Wall`;
  const signedOutHref = (s: WallSort, offset = 0) => {
    const p = new URLSearchParams();
    if (s !== "hot") p.set("sort", s);
    if (initial?.q) p.set("q", initial.q);
    if (offset) p.set("offset", String(offset));
    const qs = p.toString();
    return qs ? `?${qs}` : "?";
  };

  return (
    <div className="wall">
      <header className="wall-head">
        <div>
          <p className="shared-kicker">Insight Wall</p>
          <h1>{heading}</h1>
          <p className="muted">
            {scope === "public" || !signedIn
              ? "Answers people chose to share with everyone. Upvote the useful ones; open one to see the chart and ask your own follow-up."
              : "Answers your colleagues posted for the whole organization. Upvote the useful ones; open one to ask a follow-up."}
          </p>
        </div>
        {signedIn && !personal && (
          <div role="tablist" aria-label="Wall" className="wall-scopes">
            {(["organization", "public"] as const).map((s) => (
              <button key={s} type="button" role="tab" aria-selected={scope === s} className="wall-scope"
                      onClick={() => { setScope(s); setPage(null); }}>
                {s === "organization" ? organization ?? "Organization" : "Public"}
              </button>
            ))}
          </div>
        )}
      </header>

      <div className="wall-controls">
        <nav aria-label="Sort" className="wall-sorts">
          {WALL_SORTS.map((s) =>
            signedIn ? (
              <button key={s.id} type="button" className="wall-sort" aria-pressed={sort === s.id} onClick={() => setSort(s.id)}>
                {s.label}
              </button>
            ) : (
              <a key={s.id} className="wall-sort" aria-current={sort === s.id ? "page" : undefined} href={signedOutHref(s.id)}>
                {s.label}
              </a>
            ),
          )}
        </nav>
        <form role="search" className="wall-search" onSubmit={signedIn ? search : undefined} method="get">
          <label htmlFor={`${ids}-q`} className="sr-only">Search insights</label>
          <input id={`${ids}-q`} type="search" name="q" value={q} onChange={(e) => setQ(e.target.value)}
                 placeholder="Search titles and questions" maxLength={100} />
          {!signedIn && sort !== "hot" && <input type="hidden" name="sort" value={sort} />}
          <button type="submit" className="odn-btn odn-btn--small">Search</button>
        </form>
      </div>

      {error && <p className="error" role="alert">{error}</p>}
      {signedIn && page && !page.enabled ? (
        <p className="muted">Your organization&apos;s Insight Wall is turned off. An admin can turn it on in Organization → Sharing.</p>
      ) : items === null ? (
        <p className="muted">Loading…</p>
      ) : items.length === 0 ? (
        <p className="muted wall-empty">
          {query || initial?.q ? "No insights match your search." : "Nothing here yet. Share an answer and choose “Post to” a wall to start it."}
        </p>
      ) : (
        <div className="wall-grid">
          {items.map((c) => (
            <Card key={c.id} card={c} appUrl={appUrl} signedIn={signedIn} onVote={vote}
                  showOrganization={scope === "public" || !signedIn} />
          ))}
        </div>
      )}

      {signedIn && more && page && (
        <button type="button" className="odn-btn odn-btn--outline wall-more"
                onClick={() => load(page.items.length).catch((e: Error) => setError(e.message))}>
          More insights
        </button>
      )}
      {!signedIn && initial && (
        <nav className="wall-pages" aria-label="Pages">
          {initial.offset > 0 && <a href={signedOutHref(initial.sort, Math.max(0, initial.offset - PAGE_SIZE))}>← Previous</a>}
          {initial.items.length === PAGE_SIZE && <a href={signedOutHref(initial.sort, initial.offset + PAGE_SIZE)}>More insights →</a>}
        </nav>
      )}
    </div>
  );
}
