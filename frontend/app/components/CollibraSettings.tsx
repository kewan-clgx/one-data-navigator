"use client";

import { useCallback, useEffect, useId, useState, type FormEvent } from "react";

import { api, notify } from "@/lib/client-api";
import {
  bridgeOutdated,
  COLLIBRA_CHANGED,
  CollibraSource,
  EXTENSION_DOWNLOAD_URL,
  EXTENSION_MIN_VERSION,
  extensionSteps,
  useCollibraBridge,
  type CollibraEnv,
  type Json,
} from "@/lib/collibra";

type AdminEnv = CollibraEnv & { audience: string | null; principal_format: "email" | "email_local_part"; enabled: boolean };
type AdminListing = { environments: AdminEnv[]; token_issuer: string; jwks_url: string };
type Draft = { env: string; base_url: string; access_mode: "browser" | "token"; audience: string; principal_format: string; enabled: boolean; isNew: boolean };

const NEW: Draft = { env: "prd", base_url: "https://", access_mode: "browser", audience: "", principal_format: "email", enabled: true, isNew: true };

/** Extension status, download and steps, and a connection test per environment (what every user needs). */
export function CollibraSetup({ envs, dark = false }: { envs: CollibraEnv[]; dark?: boolean }) {
  const bridge = useCollibraBridge();
  const [tests, setTests] = useState<Record<string, Json | "testing">>({});
  const browserEnvs = envs.filter((e) => e.access_mode === "browser");
  const note = dark ? "odn-panel-note" : "muted";

  async function test(env: CollibraEnv) {
    if (env.access_mode === "browser" && !bridge) {
      setTests((t) => ({ ...t, [env.env]: { signed_in: false, error: "no_bridge", detail: "The extension is not installed in this browser yet (steps above)." } }));
      return;
    }
    setTests((t) => ({ ...t, [env.env]: "testing" }));
    const result = await new CollibraSource(env).status().catch((e: Error) => ({ signed_in: false, detail: e.message }));
    setTests((t) => ({ ...t, [env.env]: result }));
  }

  const ok = (r: Json) => r.signed_in === true || r.authenticated === true;

  return (
    <div className={`collibra-setup${dark ? " collibra-setup--dark" : ""}`}>
      {browserEnvs.length > 0 && (
        <>
          <p className={note}>
            {bridge && !bridgeOutdated(bridge)
              ? `✓ QueryMuse Collibra Bridge v${bridge.version} is installed in this browser.`
              : bridge
                ? `The installed QueryMuse Collibra Bridge (v${bridge.version}) is outdated; install v${EXTENSION_MIN_VERSION} or newer.`
                : "Collibra is read with your own Collibra sign-in through a small browser extension. Set it up once:"}
          </p>
          {(!bridge || bridgeOutdated(bridge)) && (
            <>
              <ol className="collibra-steps">
                {extensionSteps(browserEnvs[0]).map((s) => (
                  <li key={s}>{s}</li>
                ))}
              </ol>
              <p>
                <a className={`odn-btn odn-btn--small${dark ? " odn-btn--on-panel" : ""}`} href={EXTENSION_DOWNLOAD_URL} download>
                  Download extension (zip)
                </a>
              </p>
            </>
          )}
        </>
      )}
      <ul className={dark ? "odn-chat-list" : "connection-list"}>
        {envs.map((e) => {
          const r = tests[e.env];
          return (
            <li key={e.env} className={dark ? "collibra-env-row" : undefined}>
              <strong>{e.env}</strong> <span className={note}>{e.base_url}</span>{" "}
              <button type="button" className={dark ? "odn-btn odn-btn--ghost-panel odn-btn--small" : "link"} onClick={() => test(e)} disabled={r === "testing"}>
                {r === "testing" ? "Testing…" : "Test connection"}
              </button>
              {r && r !== "testing" && (
                <span role="status" className={note}>
                  {" "}
                  {ok(r) ? `✓ Connected${r.username ? ` as ${r.username}` : ""}.` : `✗ ${r.detail ?? "Not connected."}`}
                  {!ok(r) && e.access_mode === "browser" && r.error !== "no_bridge" && (
                    <>
                      {" "}
                      <a className={dark ? "collibra-panel-link" : "link"} href={e.base_url} target="_blank" rel="noreferrer">
                        Sign in to Collibra ↗
                      </a>
                    </>
                  )}
                </span>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function EnvironmentForm({ draft, issuer, jwks, onDone }: { draft: Draft; issuer: string; jwks: string; onDone: (saved: boolean) => void }) {
  const ids = useId();
  const [d, setD] = useState<Draft>(draft);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (patch: Partial<Draft>) => setD((x) => ({ ...x, ...patch }));

  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api(`admin/collibra/environments/${encodeURIComponent(d.env.trim().toLowerCase())}`, {
        method: "PUT",
        body: JSON.stringify({
          base_url: d.base_url.trim(), access_mode: d.access_mode, audience: d.access_mode === "token" ? d.audience : null,
          principal_format: d.principal_format, enabled: d.enabled,
        }),
      });
      notify(COLLIBRA_CHANGED);
      onDone(true);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="settings-form connection-form" onSubmit={save}>
      <div className="settings-grid">
        <label>
          Name
          <input value={d.env} onChange={(e) => set({ env: e.target.value })} disabled={!d.isNew} required pattern="[a-z][a-z0-9_-]{0,15}"
                 title="lowercase letters, digits, - or _, e.g. prd or int" />
        </label>
        <label>
          Collibra address
          <input type="url" value={d.base_url} onChange={(e) => set({ base_url: e.target.value })} required placeholder="https://yourcompany.collibra.com" />
        </label>
      </div>
      <fieldset>
        <legend>How users access Collibra</legend>
        <label className="connection-share">
          <input type="radio" name={`${ids}-mode`} checked={d.access_mode === "browser"} onChange={() => set({ access_mode: "browser" })} />{" "}
          Each user&apos;s own Collibra sign-in (browser extension; no Collibra setup needed)
        </label>
        <label className="connection-share">
          <input type="radio" name={`${ids}-mode`} checked={d.access_mode === "token"} onChange={() => set({ access_mode: "token" })} />{" "}
          QueryMuse sign-in (no extension; your Collibra admin must trust QueryMuse first)
        </label>
      </fieldset>
      {d.access_mode === "token" && (
        <>
          <p className="muted">
            In Collibra Console → JWT settings: JWKS URL <code>{jwks}</code>, issuer <code>{issuer}</code>, algorithm RS256,
            principal claim <code>sub</code>, and an audience matching the one below.
          </p>
          <div className="settings-grid">
            <label>
              JWT audience
              <input value={d.audience} onChange={(e) => set({ audience: e.target.value })} required placeholder={`querymuse-collibra-${d.env || "prd"}`} />
            </label>
            <label>
              Collibra username
              <select value={d.principal_format} onChange={(e) => set({ principal_format: e.target.value })}>
                <option value="email">Work email (name@company.com)</option>
                <option value="email_local_part">Part before @</option>
              </select>
            </label>
          </div>
        </>
      )}
      <label className="connection-share">
        <input type="checkbox" checked={d.enabled} onChange={(e) => set({ enabled: e.target.checked })} /> Enabled for my organization
      </label>
      <div className="settings-actions">
        <button type="submit" disabled={busy}>{busy ? "Saving…" : "Save"}</button>
        <button type="button" onClick={() => onDone(false)}>Cancel</button>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
    </form>
  );
}

/** Settings → Collibra: setup for every user; admins add, edit and remove the organization's Collibra environments. */
export default function CollibraSettings({ envs, isAdmin }: { envs: CollibraEnv[] | null; isAdmin: boolean }) {
  const ids = useId();
  const [listing, setListing] = useState<AdminListing | null>(null);
  const [editing, setEditing] = useState<Draft | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    if (isAdmin) api<AdminListing>("admin/collibra/environments").then(setListing).catch((e: Error) => setError(e.message));
  }, [isAdmin]);
  useEffect(() => {
    load();
    window.addEventListener(COLLIBRA_CHANGED, load);
    return () => window.removeEventListener(COLLIBRA_CHANGED, load);
  }, [load]);

  async function remove(env: AdminEnv) {
    if (!window.confirm(`Remove Collibra "${env.env}" (${env.base_url}) for everyone in your organization?`)) return;
    try {
      await api(`admin/collibra/environments/${encodeURIComponent(env.env)}`, { method: "DELETE" });
      notify(COLLIBRA_CHANGED);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  if (!isAdmin && !envs?.length) return null;

  return (
    <section className="settings-card" aria-labelledby={`${ids}-h`}>
      <h2 id={`${ids}-h`}>Collibra data catalog</h2>
      {envs?.length ? (
        <>
          <p className="muted">
            Your organization uses Collibra. Search business definitions and lineage in the Collibra tab, or ask in chat.
          </p>
          <CollibraSetup envs={envs} />
        </>
      ) : (
        <p className="muted">Not set up. Add your organization&apos;s Collibra to search definitions and lineage in QueryMuse.</p>
      )}
      {isAdmin && (
        <>
          <h3>Manage (admins)</h3>
          <ul className="connection-list">
            {listing?.environments.map((e) => (
              <li key={e.env}>
                <strong>{e.env}</strong> <span className="badge">{e.access_mode === "token" ? "QueryMuse sign-in" : "Browser extension"}</span>{" "}
                <span className="muted">{e.base_url}{e.enabled ? "" : " · disabled"}</span>{" "}
                <button type="button" className="link" onClick={() => setEditing({
                  env: e.env, base_url: e.base_url, access_mode: e.access_mode, audience: e.audience ?? "",
                  principal_format: e.principal_format, enabled: e.enabled, isNew: false,
                })}>Edit</button>{" "}
                <button type="button" className="link danger" onClick={() => remove(e)}>Remove</button>
              </li>
            ))}
          </ul>
          {editing ? (
            <EnvironmentForm
              key={editing.env + editing.isNew}
              draft={editing}
              issuer={listing?.token_issuer ?? ""}
              jwks={listing?.jwks_url ?? ""}
              onDone={() => setEditing(null)}
            />
          ) : (
            <button type="button" onClick={() => setEditing(NEW)}>+ Add Collibra</button>
          )}
        </>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  );
}
