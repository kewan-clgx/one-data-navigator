"use client";

import { useId, useState, type FormEvent } from "react";
import { api, CONNECTIONS_CHANGED, notify, type Connection } from "@/lib/client-api";
import { parseSnowflakeToml } from "./snowflakeToml";

type Step = { step: string; ok: boolean; detail: string | null };
export type SnowflakeDraft = {
  id?: string;
  name: string;
  account: string;
  user: string;
  warehouse: string;
  database: string;
  schema: string;
  role: string;
  allowed: string;
  private_key: string;
  private_key_passphrase: string;
  auth_method: "key_pair" | "password";
  password: string;
};

export const EMPTY_SNOWFLAKE_DRAFT: SnowflakeDraft = {
  name: "", account: "", user: "", warehouse: "", database: "", schema: "", role: "",
  allowed: "", private_key: "", private_key_passphrase: "", auth_method: "key_pair", password: "",
};

export function draftOfSnowflake(c: Connection): SnowflakeDraft {
  return {
    ...EMPTY_SNOWFLAKE_DRAFT, id: c.id, name: c.name,
    account: c.config.account ?? "", user: c.config.user ?? "", warehouse: c.config.warehouse ?? "",
    database: c.config.database ?? "", schema: c.config.schema ?? "", role: c.config.role ?? "",
    allowed: c.allowed_objects.join("\n"),
    auth_method: c.config.auth_method === "password" ? "password" : "key_pair",
  };
}

export default function SnowflakeConnectionForm({
  initial, onDone, base,
}: {
  initial: SnowflakeDraft;
  onDone: (saved: boolean) => void;
  base: string;
}) {
  const ids = useId();
  const [draft, setDraft] = useState(initial);
  const [steps, setSteps] = useState<Step[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toml, setToml] = useState("");
  const editing = !!initial.id;
  const set = (key: keyof SnowflakeDraft) => (e: { target: { value: string } }) =>
    setDraft((d) => ({ ...d, [key]: e.target.value }));
  const payload = (testing = false) => JSON.stringify({
    name: testing ? draft.name.trim() || "untitled" : draft.name,
    kind: "snowflake",
    account: draft.account,
    user: draft.user,
    warehouse: draft.warehouse,
    database: draft.database,
    schema: draft.schema,
    role: draft.role || null,
    allowed_objects: draft.allowed.split(/[\n,]+/).map((s) => s.trim()).filter(Boolean),
    private_key: draft.private_key || null,
    private_key_passphrase: draft.private_key_passphrase || null,
    auth_method: draft.auth_method,
    password: draft.password || null,
  });

  function importToml(text: string) {
    try {
      const imported = parseSnowflakeToml(text);
      setDraft((d) => ({
        ...d, ...imported, id: d.id, auth_method: "password", private_key: "", private_key_passphrase: "",
      }));
      setToml("");
      setError(null);
      setSteps(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function importFile(file: File | undefined) {
    if (!file) return;
    if (file.size > 100_000) {
      setError("TOML file is too large.");
      return;
    }
    try {
      importToml(await file.text());
    } catch {
      setError("Could not read the TOML file.");
    }
  }

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
    <form className="settings-form connection-form" onSubmit={save} aria-label="Snowflake connection">
      <details>
        <summary>Import Snowflake connections TOML</summary>
        <p className="muted">Paste or upload one [connections.name] table. Its password stays in this form until submitted.</p>
        <label>TOML configuration<textarea value={toml} onChange={(e) => setToml(e.target.value)} rows={5} autoComplete="off" /></label>
        <button type="button" onClick={() => importToml(toml)}>Import pasted TOML</button>
        <label>Upload TOML file<input type="file" accept=".toml,text/plain" onChange={(e) => {
          void importFile(e.target.files?.[0]);
          e.target.value = "";
        }} /></label>
      </details>
      <label>Connection name<input required value={draft.name} onChange={set("name")} maxLength={63} /></label>
      <label>Account identifier<input required value={draft.account} onChange={set("account")} placeholder="organization-account" /></label>
      <label>Service user login<input required value={draft.user} onChange={set("user")} /></label>
      <label>Warehouse<input required value={draft.warehouse} onChange={set("warehouse")} placeholder="ANALYTICS_WH" /></label>
      <label>Default database<input required value={draft.database} onChange={set("database")} placeholder="ANALYTICS" /></label>
      <label>Default schema<input required value={draft.schema} onChange={set("schema")} placeholder="PUBLIC" /></label>
      <label>Role (optional)<input value={draft.role} onChange={set("role")} placeholder="QUERYMUSE_READER" /></label>
      <p className="muted">Use uppercase unquoted warehouse, database, schema and role names. Queries can access other databases in the allowlist. Snowflake warehouse usage may incur credits.</p>
      <label>Allowed database.schema.table patterns
        <textarea required value={draft.allowed} onChange={set("allowed")} placeholder={"ANALYTICS.PUBLIC.*\nOTHER.PUBLIC.ORDERS"} rows={3} />
      </label>
      <p className="muted">One pattern per line; names match exact Snowflake case. Use * for an entire component.</p>
      {draft.role.toUpperCase() === "ACCOUNTADMIN" && <p className="error" role="alert">ACCOUNTADMIN is highly privileged. Use a dedicated read-only role instead.</p>}
      <label>Authentication
        <select value={draft.auth_method} onChange={(e) => setDraft((d) => ({
          ...d, auth_method: e.target.value === "password" ? "password" : "key_pair",
          password: "", private_key: "", private_key_passphrase: "",
        }))}>
          <option value="key_pair">Key pair</option>
          <option value="password">Password</option>
        </select>
      </label>
      {draft.auth_method === "password" ? (
        <label>Password{editing && initial.auth_method === "password" && " — leave blank to keep the saved password"}
          <input type="password" autoComplete="new-password" required={!editing || initial.auth_method !== "password"} value={draft.password} onChange={set("password")} />
        </label>
      ) : (
        <>
          <label htmlFor={`${ids}-key`}>RSA private key (PEM){editing && initial.auth_method === "key_pair" && " — leave blank to keep the saved key"}</label>
          <textarea id={`${ids}-key`} required={!editing || initial.auth_method !== "key_pair"} value={draft.private_key} onChange={set("private_key")} rows={3} autoComplete="off" />
          <label>Private key passphrase (if encrypted)
            <input type="password" autoComplete="new-password" value={draft.private_key_passphrase} onChange={set("private_key_passphrase")} />
          </label>
        </>
      )}
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
