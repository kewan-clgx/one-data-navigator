"use client";

import { useCallback, useId, useState, type FormEvent } from "react";

import { api, CONNECTIONS_CHANGED, notify } from "@/lib/client-api";
import SSHTunnelSetup from "./SSHTunnelSetup";

type Step = { step: string; ok: boolean; detail: string | null };
const STEP_LABELS: Record<string, string> = {
  tunnel: "SSH tunnel",
  auth: "Authentication",
  network: "Network",
  metadata: "Metadata",
  read: "Read check",
};

type Draft = {
  id?: string;
  name: string;
  host: string;
  port: string;
  database: string;
  user: string;
  password: string;
  ssl_mode: string;
  schema: string;
  allowed: string;
  use_ssh_tunnel: boolean;
  bastion_host: string;
  bastion_port: string;
  bastion_user: string;
  ssh_private_key: string;
  known_hosts_fingerprint: string;
};

const EMPTY: Draft = {
  name: "",
  host: "",
  port: "5432",
  database: "",
  user: "",
  password: "",
  ssl_mode: "prefer",
  schema: "public",
  allowed: "",
  use_ssh_tunnel: false,
  bastion_host: "",
  bastion_port: "22",
  bastion_user: "",
  ssh_private_key: "",
  known_hosts_fingerprint: "",
};

function draftOf(c: any): Draft {
  const config = c.config || {};
  return {
    id: c.id,
    name: c.name || "",
    host: config.host || "",
    port: String(config.port || "5432"),
    database: config.database || "",
    user: config.user || "",
    password: "",
    ssl_mode: config.ssl_mode || "prefer",
    schema: config.schema || "public",
    allowed: c.allowed_objects?.join("\n") || "",
    use_ssh_tunnel: !!config.ssh_tunnel,
    bastion_host: config.ssh_tunnel?.bastion_host || "",
    bastion_port: String(config.ssh_tunnel?.bastion_port || "22"),
    bastion_user: config.ssh_tunnel?.bastion_user || "",
    ssh_private_key: "",
    known_hosts_fingerprint: config.ssh_tunnel?.known_hosts_fingerprint || "",
  };
}

export default function PostgresConnectionForm({
  initial,
  onDone,
  base = "me/connections",
}: {
  initial: Draft;
  onDone: (saved: boolean) => void;
  /** API collection: "me/connections" (personal) or "connections/shared" (organization). */
  base?: string;
}) {
  const ids = useId();
  const [d, setD] = useState<Draft>(initial);
  const [steps, setSteps] = useState<Step[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const editing = !!initial.id;

  const set = (k: keyof Draft) => (e: { target: { value: string } }) => setD((v) => ({ ...v, [k]: e.target.value }));
  const setCheck = (k: keyof Draft) => (e: { target: { checked: boolean } }) => setD((v) => ({ ...v, [k]: e.target.checked }));

  function payload(forTest = false): string {
    const config: any = {
      host: d.host,
      port: parseInt(d.port, 10),
      database: d.database,
      user: d.user,
    };
    if (d.password) config.password = d.password;
    if (d.ssl_mode) config.ssl_mode = d.ssl_mode;
    if (d.schema && d.schema !== "public") config.schema = d.schema;
    
    if (d.use_ssh_tunnel) {
      config.ssh_tunnel = {
        bastion_host: d.bastion_host,
        bastion_port: parseInt(d.bastion_port, 10),
        bastion_user: d.bastion_user,
        // Empty on edit = keep the saved key (the server never sends it back).
        ssh_private_key: d.ssh_private_key.trim() ? d.ssh_private_key : null,
        known_hosts_fingerprint: d.known_hosts_fingerprint || null,
      };
    }

    return JSON.stringify({
      name: forTest ? d.name.trim() || "untitled" : d.name,
      kind: "postgres",
      allowed_objects: d.allowed.split(/[\n,]+/).map((s) => s.trim()).filter(Boolean),
      ...config,
    });
  }

  async function onTest() {
    setBusy(true);
    setError(null);
    setSteps(null);
    try {
      const q = editing ? `?connection_id=${initial.id}` : "";
      setSteps(await api<Step[]>(`${base}/test${q}`, { method: "POST", body: payload(true) }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api(editing ? `${base}/${initial.id}` : base, {
        method: editing ? "PUT" : "POST",
        body: payload(),
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
    <form onSubmit={onSubmit} className="settings-form connection-form" aria-label={editing ? "Edit Postgres connection" : "New Postgres connection"}>
      <label>
        Connection name
        <input required value={d.name} onChange={set("name")} placeholder="e.g. analytics-db" maxLength={63} />
      </label>

      <fieldset>
        <legend>Connection details</legend>
        <div className="settings-grid">
          <label>
            Host
            <input required value={d.host} onChange={set("host")} placeholder="localhost or IP" />
          </label>
          <label>
            Port
            <input required type="number" value={d.port} onChange={set("port")} min="1" max="65535" />
          </label>
          <label>
            Database
            <input required value={d.database} onChange={set("database")} placeholder="mydb" />
          </label>
          <label>
            User
            <input required value={d.user} onChange={set("user")} placeholder="postgres" />
          </label>
          <label>
            Password
            <input type="password" value={d.password} onChange={set("password")} placeholder={editing ? "Leave empty to keep saved password" : "Required"} />
          </label>
        </div>
      </fieldset>

      <label className="checkbox-label">
        <input type="checkbox" checked={d.use_ssh_tunnel} onChange={setCheck("use_ssh_tunnel")} />
        Use SSH tunnel (optional)
      </label>

      {d.use_ssh_tunnel && (
        <SSHTunnelSetup
          d={d}
          setD={setD}
          hasStoredKey={editing && initial.use_ssh_tunnel}
        />
      )}

      <label className="toggle-advanced">
        <input
          type="checkbox"
          checked={showAdvanced}
          onChange={(e) => setShowAdvanced(e.target.checked)}
        />
        Advanced options
      </label>

      {showAdvanced && (
        <fieldset>
          <legend>Advanced</legend>
          <label>
            SSL mode
            <select value={d.ssl_mode} onChange={set("ssl_mode")}>
              <option value="disable">disable</option>
              <option value="allow">allow</option>
              <option value="prefer">prefer</option>
              <option value="require">require</option>
              <option value="verify-ca">verify-ca</option>
              <option value="verify-full">verify-full</option>
            </select>
          </label>
          <label>
            Schema (for metadata discovery)
            <input value={d.schema} onChange={set("schema")} placeholder="public" />
          </label>
          <label htmlFor={`${ids}-allowed`}>Specific tables you can query (optional)</label>
          <textarea
            id={`${ids}-allowed`}
            rows={3}
            value={d.allowed}
            onChange={set("allowed")}
            placeholder={"One per line: schema.table\nEmpty = all tables you can read"}
            spellCheck={false}
          />
        </fieldset>
      )}

      <div className="settings-actions">
        <button type="button" onClick={onTest} disabled={busy}>Test connection</button>
        <button type="submit" disabled={busy}>{editing ? "Save changes" : "Add connection"}</button>
        <button type="button" onClick={() => onDone(false)} disabled={busy}>Cancel</button>
      </div>

      {steps && (
        <ol className="test-steps" aria-label="Connection test">
          {steps.map((s) => (
            <li key={s.step} className={s.ok ? "ok" : "error"}>
              {s.ok ? "✓" : "✗"} {STEP_LABELS[s.step] ?? s.step}
              {s.detail && <span className="muted"> — {s.detail}</span>}
            </li>
          ))}
        </ol>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </form>
  );
}

export { EMPTY as EMPTY_POSTGRES_DRAFT, draftOf as draftOfPostgres };
