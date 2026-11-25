"use client";

import { useState, type FormEvent } from "react";
import { api, CONNECTIONS_CHANGED, notify, type Connection } from "@/lib/client-api";

type Step = { step: string; ok: boolean; detail: string | null };
export type MySQLDraft = {
  id?: string;
  name: string;
  host: string;
  port: string;
  database: string;
  user: string;
  password: string;
  ssl_ca: string;
  allowed: string;
};

export const EMPTY_MYSQL_DRAFT: MySQLDraft = {
  name: "", host: "", port: "3306", database: "", user: "", password: "", ssl_ca: "", allowed: "",
};

export function draftOfMySQL(c: Connection): MySQLDraft {
  return {
    ...EMPTY_MYSQL_DRAFT, id: c.id, name: c.name,
    host: c.config.host ?? "", port: String(c.config.port ?? 3306),
    database: c.config.database ?? "", user: c.config.user ?? "", ssl_ca: c.config.ssl_ca ?? "",
    allowed: c.allowed_objects.join("\n"),
  };
}

export default function MySQLConnectionForm({
  initial, onDone, base,
}: {
  initial: MySQLDraft;
  onDone: (saved: boolean) => void;
  base: string;
}) {
  const [draft, setDraft] = useState(initial);
  const [steps, setSteps] = useState<Step[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const editing = !!initial.id;
  const set = (key: keyof MySQLDraft) => (e: { target: { value: string } }) => {
    setDraft((d) => ({ ...d, [key]: e.target.value }));
    setSteps(null);
  };
  const payload = (testing = false) => JSON.stringify({
    name: testing ? draft.name.trim() || "untitled" : draft.name,
    kind: "mysql", host: draft.host, port: Number(draft.port), database: draft.database, user: draft.user,
    password: draft.password || null, ssl_ca: draft.ssl_ca || null,
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
      await api(editing ? `${base}/${initial.id}` : base, {
        method: editing ? "PUT" : "POST", body: payload(),
      });
      notify(CONNECTIONS_CHANGED);
      onDone(true);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="settings-form connection-form" onSubmit={save} aria-label="MySQL connection">
      <p className="muted">MySQL 8.0 and 8.4. Use a SELECT-only account. An operator must approve this hostname, port and destination IP ranges before connecting.</p>
      <label>Connection name<input required value={draft.name} onChange={set("name")} maxLength={63} /></label>
      <label>Hostname<input required value={draft.host} onChange={set("host")} placeholder="mysql.example.com" maxLength={253} /></label>
      <label>Port<input required type="number" min={1} max={65535} value={draft.port} onChange={set("port")} /></label>
      <label>Default database<input required value={draft.database} onChange={set("database")} maxLength={64} /></label>
      <p className="muted">Other accessible databases can be browsed and queried when allowed below.</p>
      <label>Username<input required value={draft.user} onChange={set("user")} maxLength={32} /></label>
      <label>Password{editing && " — leave blank to keep the saved password"}
        <input type="password" autoComplete="new-password" required={!editing} value={draft.password} onChange={set("password")} />
      </label>
      <label>CA certificate bundle (optional PEM)
        <textarea value={draft.ssl_ca} onChange={set("ssl_ca")} rows={4} maxLength={100000} spellCheck={false} placeholder="-----BEGIN CERTIFICATE-----" />
      </label>
      <p className="muted">TLS certificate and hostname verification are always enabled. Leave the CA blank for system trust; clear it on edit to remove a custom CA. Certificates only, never private keys.</p>
      <label>Allowed database.table patterns
        <textarea required value={draft.allowed} onChange={set("allowed")} rows={3} placeholder={"analytics.*\nreporting.Orders"} />
      </label>
      <p className="muted">One pattern per line; use exact catalog case and * for an entire component. System databases are excluded.</p>
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
