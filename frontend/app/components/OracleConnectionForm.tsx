"use client";

import { useState, type FormEvent } from "react";
import { api, CONNECTIONS_CHANGED, notify, type Connection } from "@/lib/client-api";

type Step = { step: string; ok: boolean; detail: string | null };
export type OracleDraft = {
  id?: string;
  name: string;
  host: string;
  port: string;
  service_name: string;
  schema: string;
  user: string;
  password: string;
  ssl_ca: string;
  allowed: string;
};

export const EMPTY_ORACLE_DRAFT: OracleDraft = {
  name: "", host: "", port: "2484", service_name: "", schema: "", user: "", password: "", ssl_ca: "", allowed: "",
};

export function draftOfOracle(c: Connection): OracleDraft {
  return {
    ...EMPTY_ORACLE_DRAFT, id: c.id, name: c.name,
    host: c.config.host ?? "", port: String(c.config.port ?? 2484),
    service_name: c.config.service_name ?? "", schema: c.config.schema ?? "", user: c.config.user ?? "",
    ssl_ca: c.config.ssl_ca ?? "", allowed: c.allowed_objects.join("\n"),
  };
}

export default function OracleConnectionForm({
  initial, onDone, base,
}: {
  initial: OracleDraft;
  onDone: (saved: boolean) => void;
  base: string;
}) {
  const [draft, setDraft] = useState(initial);
  const [steps, setSteps] = useState<Step[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const editing = !!initial.id;
  const set = (key: keyof OracleDraft) => (e: { target: { value: string } }) => {
    setDraft((d) => ({ ...d, [key]: e.target.value }));
    setSteps(null);
  };
  const payload = (testing = false) => JSON.stringify({
    name: testing ? draft.name.trim() || "untitled" : draft.name,
    kind: "oracle", host: draft.host, port: Number(draft.port), service_name: draft.service_name,
    schema: draft.schema, user: draft.user, password: draft.password || null, ssl_ca: draft.ssl_ca || null,
    allowed_objects: draft.allowed.split(/[\n,]+/).map((s) => s.trim()).filter(Boolean),
  });

  async function test() {
    setBusy(true);
    setError(null);
    setSteps(null);
    try {
      const suffix = editing ? `?connection_id=${initial.id}` : "";
      setSteps(await api<Step[]>(`${base}/test${suffix}`, { method: "POST", body: payload(true) }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api(editing ? `${base}/${initial.id}` : base, { method: editing ? "PUT" : "POST", body: payload() });
      notify(CONNECTIONS_CHANGED);
      onDone(true);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="settings-form connection-form" onSubmit={save} aria-label="Oracle connection">
      <p className="muted">Oracle 19c / Database Free, one service or PDB with multiple schemas. Use a dedicated read-only account. An operator must approve the endpoint and IP ranges at the egress proxy.</p>
      <label>Connection name<input required value={draft.name} onChange={set("name")} maxLength={63} /></label>
      <label>Hostname<input required value={draft.host} onChange={set("host")} placeholder="oracle.example.com" maxLength={253} /></label>
      <label>TCPS listener port<input required type="number" min={1} max={65535} value={draft.port} onChange={set("port")} /></label>
      <p className="muted">Use the actual TLS listener port; 2484 is only a suggested default.</p>
      <label>Service name<input required value={draft.service_name} onChange={set("service_name")} maxLength={255} placeholder="reporting.example.com" /></label>
      <label>Default schema<input required value={draft.schema} onChange={set("schema")} maxLength={128} placeholder="ANALYTICS" /></label>
      <label>Username<input required value={draft.user} onChange={set("user")} maxLength={128} /></label>
      <label>Password{editing && " - leave blank to keep the saved password"}
        <input type="password" autoComplete="new-password" required={!editing} value={draft.password} onChange={set("password")} />
      </label>
      <label>CA certificate bundle (optional PEM)
        <textarea value={draft.ssl_ca} onChange={set("ssl_ca")} rows={4} maxLength={100000} spellCheck={false} placeholder="-----BEGIN CERTIFICATE-----" />
      </label>
      <p className="muted">Verified TCPS is mandatory and stays end-to-end through the proxy. No wallet, private key, SID or Oracle Net descriptor. Clear the CA on edit to return to system trust.</p>
      <label>Allowed schema.table patterns
        <textarea required value={draft.allowed} onChange={set("allowed")} rows={3} placeholder={"ANALYTICS.*\nREPORTING.Orders"} />
      </label>
      <p className="muted">Exact catalog case; * matches a whole component. Unquoted SQL names resolve uppercase. Synonyms, database links and system objects are excluded.</p>
      {steps && <ol className="test-steps" aria-label="Connection test">{steps.map((s) =>
        <li key={s.step}>{s.ok ? "✓" : "✗"} {s.step}{s.detail ? `: ${s.detail}` : ""}</li>)}</ol>}
      {error && <p className="error" role="alert">{error}</p>}
      <div className="settings-actions">
        <button type="button" onClick={test} disabled={busy}>Test connection</button>
        <button type="submit" disabled={busy}>Save connection</button>
        <button type="button" onClick={() => onDone(false)} disabled={busy}>Cancel</button>
      </div>
    </form>
  );
}
