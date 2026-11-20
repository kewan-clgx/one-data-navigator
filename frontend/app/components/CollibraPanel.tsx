"use client";

import { useEffect, useMemo, useState, type FormEvent } from "react";

import { bridgeOutdated, CollibraError, CollibraSource, useCollibraBridge, type CollibraEnv, type Json } from "@/lib/collibra";

import type { CollibraScope } from "./CollibraBrowserTools";
import { CollibraSetup } from "./CollibraSettings";

type Asset = { id: string; name: string; display_name: string | null; type: string | null; domain: string | null; status: string | null; url: string | null };
type Relation = { source: { id: string; name: string } | null; target: { id: string; name: string } | null };
type Detail = Asset & { definition: string | null; attributes: { type: string; value: unknown }[]; profiling_attributes_omitted?: number; error?: string };

function errorText(e: unknown, env: CollibraEnv) {
  const err = e as CollibraError;
  return { message: err.message, loginUrl: err.loginUrl ?? (env.access_mode === "browser" ? env.base_url : undefined) };
}

function AssetLine({ a }: { a: Asset }) {
  return (
    <>
      <span className="odn-chat-title">{a.display_name || a.name}</span>
      <span className="odn-chat-meta">{[a.type, a.domain, a.status].filter(Boolean).join(" · ")}</span>
    </>
  );
}

/** Collibra side-panel tab: search the organization's Collibra, read an asset, and scope the chat to it. */
export default function CollibraPanel({
  envs,
  scope,
  onScopeChange,
}: {
  envs: CollibraEnv[];
  scope: CollibraScope | null;
  onScopeChange: (scope: CollibraScope | null) => void;
}) {
  const bridge = useCollibraBridge();
  const [envName, setEnvName] = useState(envs[0]?.env ?? "");
  const env = envs.find((e) => e.env === envName) ?? envs[0];
  const source = useMemo(() => (env ? new CollibraSource(env) : null), [env]);
  const [text, setText] = useState("");
  const [match, setMatch] = useState("ANYWHERE");
  const [results, setResults] = useState<{ items: Asset[]; total: number; next: number | null } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ message: string; loginUrl?: string } | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [relations, setRelations] = useState<{ outgoing: Relation[]; incoming: Relation[] } | null>(null);

  const needsBridge = env?.access_mode === "browser" && (!bridge || bridgeOutdated(bridge));

  useEffect(() => {
    if (!selected || !source || !env) return;
    let live = true;
    setDetail(null);
    setRelations(null);
    setError(null);
    Promise.all([source.describe([selected]), source.relations(selected, "outgoing", 25), source.relations(selected, "incoming", 25)])
      .then(([d, out, inc]: Json[]) => {
        if (!live) return;
        const asset = d.assets?.[0] as Detail | undefined;
        if (asset?.error) setError({ message: asset.error, loginUrl: env.access_mode === "browser" ? env.base_url : undefined });
        setDetail(asset ?? null);
        setRelations({ outgoing: out.results ?? [], incoming: inc.results ?? [] });
      })
      .catch((e) => live && setError(errorText(e, env)));
    return () => {
      live = false;
    };
  }, [selected, source, env]);

  async function search(e: FormEvent | null, offset = 0) {
    e?.preventDefault();
    if (!source || !env || !text.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const page = await source.search(text.trim(), match, 25, offset);
      setResults((prev) => ({
        items: offset && prev ? [...prev.items, ...page.results] : page.results,
        total: page.total,
        next: page.next_offset ?? null,
      }));
      if (!offset) setSelected(null);
    } catch (err) {
      setError(errorText(err, env));
    } finally {
      setBusy(false);
    }
  }

  if (!env) return <div className="odn-list-panel"><p className="odn-panel-note">Collibra is not set up.</p></div>;

  const rel = (r: Relation, dir: "outgoing" | "incoming") => (dir === "outgoing" ? r.target : r.source);

  return (
    <div className="sidebar collibra-panel">
      {envs.length > 1 && (
        <label>
          Environment
          <select value={env.env} onChange={(e) => { setEnvName(e.target.value); setResults(null); setSelected(null); }}>
            {envs.map((e) => (
              <option key={e.env} value={e.env}>{e.env} · {new URL(e.base_url).host}</option>
            ))}
          </select>
        </label>
      )}
      {needsBridge ? (
        <CollibraSetup envs={[env]} dark />
      ) : (
        <>
          <form className="search-row collibra-search" onSubmit={(e) => search(e)} role="search">
            <label>
              Search Collibra
              <input type="search" value={text} onChange={(e) => setText(e.target.value)} placeholder="e.g. corporate revenue" />
            </label>
            <select aria-label="Name match" value={match} onChange={(e) => setMatch(e.target.value)}>
              <option value="ANYWHERE">contains</option>
              <option value="START">starts with</option>
              <option value="EXACT">exact</option>
            </select>
            <button type="submit" className="odn-btn odn-btn--on-panel odn-btn--small" disabled={busy || !text.trim()}>
              {busy ? "Searching…" : "Search"}
            </button>
          </form>

          {error && (
            <p className="error" role="alert">
              {error.message}{" "}
              {error.loginUrl && (
                <a className="collibra-panel-link" href={error.loginUrl} target="_blank" rel="noreferrer">Sign in to Collibra ↗</a>
              )}
            </p>
          )}

          {selected ? (
            <div className="collibra-detail">
              <button type="button" className="odn-btn odn-btn--ghost-panel odn-btn--small collibra-back" onClick={() => setSelected(null)}>← Results</button>
              {!detail && !error && <p className="odn-panel-note">Loading…</p>}
              {detail && !detail.error && (
                <>
                  <h3 className="collibra-title">{detail.display_name || detail.name}</h3>
                  <p className="odn-panel-note">{[detail.type, detail.domain, detail.status].filter(Boolean).join(" · ")}</p>
                  {detail.display_name && detail.display_name !== detail.name && <p className="odn-panel-note collibra-name"><code>{detail.name}</code></p>}
                  <p className="collibra-definition">{detail.definition ?? <span className="odn-panel-note">No definition in Collibra.</span>}</p>
                  <div className="odn-selection-actions">
                    <button
                      type="button"
                      className="odn-btn odn-btn--on-panel odn-btn--small"
                      aria-pressed={scope?.id === detail.id}
                      onClick={() => onScopeChange(scope?.id === detail.id ? null : {
                        env: env.env, id: detail.id, name: detail.display_name || detail.name, type: detail.type, url: detail.url,
                      })}
                    >
                      {scope?.id === detail.id ? "Used in chat ✓" : "Use in chat"}
                    </button>
                    {detail.url && (
                      <a className="odn-btn odn-btn--ghost-panel odn-btn--small" href={detail.url} target="_blank" rel="noreferrer">Open in Collibra ↗</a>
                    )}
                  </div>
                  {detail.attributes.length > 0 && (
                    <>
                      <span className="odn-panel-heading">Attributes</span>
                      <dl className="collibra-attrs">
                        {detail.attributes.map((a, i) => (
                          <div key={`${a.type}-${i}`}>
                            <dt>{a.type}</dt>
                            <dd>{String(a.value)}</dd>
                          </div>
                        ))}
                      </dl>
                      {!!detail.profiling_attributes_omitted && (
                        <p className="odn-panel-note">{detail.profiling_attributes_omitted} profiling statistics not shown.</p>
                      )}
                    </>
                  )}
                  {relations && (["outgoing", "incoming"] as const).map((dir) =>
                    relations[dir].length ? (
                      <div key={dir}>
                        <span className="odn-panel-heading">{dir === "outgoing" ? "Related (outgoing)" : "Related (incoming)"}</span>
                        <ul className="odn-chat-list">
                          {relations[dir].map((r, i) => {
                            const other = rel(r, dir);
                            return other ? (
                              <li key={`${other.id}-${i}`} className="odn-chat-item">
                                <button type="button" className="odn-chat-open" onClick={() => setSelected(other.id)}>
                                  <span className="odn-chat-title">{other.name}</span>
                                </button>
                              </li>
                            ) : null;
                          })}
                        </ul>
                      </div>
                    ) : null,
                  )}
                </>
              )}
            </div>
          ) : (
            results && (
              <div className="collibra-results">
                <p className="odn-panel-note">{results.total.toLocaleString()} match{results.total === 1 ? "" : "es"}</p>
                <ul className="odn-chat-list">
                  {results.items.map((a) => (
                    <li key={a.id} className={`odn-chat-item${scope?.id === a.id ? " odn-chat-item--active" : ""}`}>
                      <button type="button" className="odn-chat-open" onClick={() => setSelected(a.id)} title={a.name}>
                        <AssetLine a={a} />
                      </button>
                    </li>
                  ))}
                </ul>
                {results.next !== null && (
                  <button type="button" className="odn-btn odn-btn--ghost-panel odn-btn--small" disabled={busy} onClick={() => search(null, results.next ?? 0)}>
                    More
                  </button>
                )}
              </div>
            )
          )}
          {!results && !selected && !error && (
            <p className="odn-panel-note">
              Search business terms, columns and tables in {new URL(env.base_url).host}
              {env.access_mode === "browser" ? " with your own Collibra sign-in." : "."} Pick one to read its definition and relations, or use it in chat.
            </p>
          )}
        </>
      )}
    </div>
  );
}
