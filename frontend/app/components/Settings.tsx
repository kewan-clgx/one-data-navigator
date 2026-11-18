"use client";

import { useCallback, useEffect, useId, useState, type FormEvent } from "react";

import { api, CONNECTIONS_CHANGED, LLM_CHANGED, notify, type Budget, type Connection } from "@/lib/client-api";
import PostgresConnectionForm, { EMPTY_POSTGRES_DRAFT, draftOfPostgres } from "./PostgresConnectionForm";

// ---------------------------------------------------------------------------------------------
// AI model (bring your own key)

type Provider = "gemini" | "openai" | "anthropic" | "openai_compatible";

const PROVIDERS: { id: Provider; label: string; keyHelp: string; modelHint: string }[] = [
  { id: "gemini", label: "Google Gemini (AI Studio)", keyHelp: "aistudio.google.com/apikey", modelHint: "e.g. gemini-2.5-flash" },
  { id: "openai", label: "OpenAI", keyHelp: "platform.openai.com/api-keys", modelHint: "e.g. gpt-4.1" },
  { id: "anthropic", label: "Anthropic", keyHelp: "console.anthropic.com/settings/keys", modelHint: "e.g. claude-sonnet-4-5" },
  {
    id: "openai_compatible",
    label: "OpenAI-compatible endpoint",
    keyHelp: "your provider or LiteLLM proxy",
    modelHint: "model name served by the endpoint",
  },
];

type LlmKey = { provider: Provider; model: string; api_base: string | null; key_hint: string; updated_at: string };
type LlmTest = { ok: boolean; error: string | null; models: string[]; model_found: boolean | null };

function TrialStatus({ budget }: { budget: Budget | null }) {
  if (!budget) return null;
  if (budget.key_source === "user") {
    return <p className="muted">Chat uses your own key. Usage is billed by your provider; no platform limit applies.</p>;
  }
  const used = budget.user_tokens_used_today ?? 0;
  const limit = budget.user_daily_token_limit ?? 0;
  return (
    <p className={budget.exceeded ? "error" : "muted"}>
      {limit > 0
        ? `Free trial on the shared model: ${used.toLocaleString()} of ${limit.toLocaleString()} tokens used today.`
        : "No free trial is available on the shared model."}{" "}
      {budget.exceeded && "Today's trial is used up. "}Add your own key to keep chatting without limits.
    </p>
  );
}

function LlmKeySection() {
  const ids = useId();
  const [current, setCurrent] = useState<LlmKey | null>(null);
  const [budget, setBudget] = useState<Budget | null>(null);
  const [provider, setProvider] = useState<Provider>("gemini");
  const [model, setModel] = useState("");
  const [apiBase, setApiBase] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [test, setTest] = useState<LlmTest | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  const load = useCallback(async () => {
    const [key, b] = await Promise.all([api<LlmKey | null>("me/llm-key"), api<Budget>("llm-budget")]);
    setCurrent(key);
    setBudget(b);
    if (key) {
      setProvider(key.provider);
      setModel(key.model);
      setApiBase(key.api_base ?? "");
    }
  }, []);

  useEffect(() => {
    load().catch((e: Error) => setMessage({ kind: "error", text: e.message }));
  }, [load]);

  const info = PROVIDERS.find((p) => p.id === provider)!;
  const sameProvider = current?.provider === provider && (provider !== "openai_compatible" || current.api_base === apiBase.replace(/\/+$/, ""));
  const body = () => JSON.stringify({ provider, model, api_base: provider === "openai_compatible" ? apiBase : null, api_key: apiKey || null });

  async function run<T>(fn: () => Promise<T>): Promise<T | undefined> {
    setBusy(true);
    setMessage(null);
    try {
      return await fn();
    } catch (e) {
      setMessage({ kind: "error", text: (e as Error).message });
      return undefined;
    } finally {
      setBusy(false);
    }
  }

  async function onTest() {
    setTest(null);
    const result = await run(() => api<LlmTest>("me/llm-key/test", { method: "POST", body: body() }));
    if (result) setTest(result);
  }

  async function onSave(e: FormEvent) {
    e.preventDefault();
    const saved = await run(() => api<LlmKey>("me/llm-key", { method: "PUT", body: body() }));
    if (saved) {
      setApiKey("");
      await load();
      notify(LLM_CHANGED);
      setMessage({ kind: "ok", text: "Saved. The chat now uses your key." });
    }
  }

  async function onRemove() {
    if (!window.confirm("Remove your API key? Chat falls back to the shared trial model.")) return;
    setBusy(true);
    setMessage(null);
    try {
      await api<void>("me/llm-key", { method: "DELETE" });
      setCurrent(null);
      setApiKey("");
      setTest(null);
      await load();
      notify(LLM_CHANGED);
      setMessage({ kind: "ok", text: "Key removed." });
    } catch (e) {
      setMessage({ kind: "error", text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="settings-card" aria-labelledby={`${ids}-h`}>
      <h2 id={`${ids}-h`}>AI model</h2>
      <p className="muted">
        Use your own API key to power the chat assistant at your own provider&apos;s cost. Keys are encrypted at rest and
        only used for your requests.
      </p>
      <TrialStatus budget={budget} />
      <form onSubmit={onSave} className="settings-form">
        <label>
          Provider
          <select value={provider} onChange={(e) => { setProvider(e.target.value as Provider); setTest(null); }}>
            {PROVIDERS.map((p) => (
              <option key={p.id} value={p.id}>{p.label}</option>
            ))}
          </select>
        </label>
        {provider === "openai_compatible" && (
          <label>
            Endpoint URL
            <input
              type="url"
              required
              value={apiBase}
              onChange={(e) => setApiBase(e.target.value)}
              placeholder="https://llm.example.com/v1"
            />
          </label>
        )}
        <label>
          Model
          <input
            required
            value={model}
            onChange={(e) => setModel(e.target.value)}
            placeholder={info.modelHint}
            list={`${ids}-models`}
            autoComplete="off"
          />
          <datalist id={`${ids}-models`}>
            {test?.models.map((m) => <option key={m} value={m} />)}
          </datalist>
        </label>
        <label>
          API key
          <input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            required={!sameProvider}
            autoComplete="off"
            placeholder={sameProvider && current ? `Saved key ${current.key_hint} — leave blank to keep` : `From ${info.keyHelp}`}
          />
        </label>
        <div className="settings-actions">
          <button type="button" onClick={onTest} disabled={busy || (!apiKey && !sameProvider)}>Test key</button>
          <button type="submit" disabled={busy}>Save</button>
          {current && <button type="button" className="danger" onClick={onRemove} disabled={busy}>Remove key</button>}
        </div>
      </form>
      {test && (
        <p className={test.ok ? "ok" : "error"} role="status">
          {test.ok
            ? `Key works. ${test.models.length} models available${test.model_found === false ? `; "${model}" is not among them (pick one from the Model list)` : test.model_found ? `, including "${model}"` : ""}.`
            : `Test failed: ${test.error}`}
        </p>
      )}
      {message && <p className={message.kind === "ok" ? "ok" : "error"} role="status">{message.text}</p>}
    </section>
  );
}

// ---------------------------------------------------------------------------------------------
// Data connections (DBeaver-style), BigQuery first

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
  project: string;
  location: string;
  billing_project: string;
  default_dataset: string;
  allowed: string;
  credentials_json: string;
};

const EMPTY: Draft = { name: "", project: "", location: "", billing_project: "", default_dataset: "", allowed: "", credentials_json: "" };

function draftOf(c: Connection): Draft {
  const project = c.config.project ?? "";
  const allAccess = c.allowed_objects.length === 1 && c.allowed_objects[0] === `${project}.*.*`;
  return {
    id: c.id,
    name: c.name,
    project,
    location: c.config.location ?? "",
    billing_project: c.config.billing_project ?? "",
    default_dataset: c.config.default_dataset ?? "",
    allowed: allAccess ? "" : c.allowed_objects.join("\n"),
    credentials_json: "",
  };
}

function ConnectionForm({ initial, onDone }: { initial: Draft; onDone: (saved: boolean) => void }) {
  const ids = useId();
  const [d, setD] = useState<Draft>(initial);
  const [steps, setSteps] = useState<Step[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // An uploaded key is kept out of the text box so the private key is never displayed.
  const [fileKey, setFileKey] = useState<{ json: string; label: string } | null>(null);
  const editing = !!initial.id;

  const set = (k: keyof Draft) => (e: { target: { value: string } }) => setD((v) => ({ ...v, [k]: e.target.value }));

  function payload(forTest = false): string {
    return JSON.stringify({
      // The name only matters when saving; a test works before one is chosen.
      name: forTest ? d.name.trim() || "untitled" : d.name,
      kind: "bigquery",
      project: d.project,
      location: d.location,
      billing_project: d.billing_project,
      default_dataset: d.default_dataset,
      allowed_objects: d.allowed.split(/[\n,]+/).map((s) => s.trim()).filter(Boolean),
      credentials_json: fileKey?.json || d.credentials_json || null,
    });
  }

  async function onFile(file: File | undefined) {
    if (!file) {
      setFileKey(null);
      return;
    }
    const text = await file.text();
    let parsed: { project_id?: string; client_email?: string } = {};
    try {
      parsed = JSON.parse(text);
    } catch {
      /* validated by the server */
    }
    setFileKey({ json: text, label: parsed.client_email ? `Loaded key for ${parsed.client_email}` : `Loaded ${file.name}` });
    setD((v) => ({ ...v, credentials_json: "", project: v.project || parsed.project_id || "" }));
  }

  async function onTest() {
    setBusy(true);
    setError(null);
    setSteps(null);
    try {
      const q = editing ? `?connection_id=${initial.id}` : "";
      setSteps(await api<Step[]>(`me/connections/test${q}`, { method: "POST", body: payload(true) }));
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
      await api(editing ? `me/connections/${initial.id}` : "me/connections", {
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
    <form onSubmit={onSubmit} className="settings-form connection-form" aria-label={editing ? "Edit connection" : "New connection"}>
      <label>
        Connection name
        <input required value={d.name} onChange={set("name")} placeholder="e.g. my-analytics" maxLength={63} />
      </label>
      <fieldset>
        <legend>Service-account key (JSON)</legend>
        <p className="muted">
          {editing
            ? "Leave empty to keep the saved key, or upload/paste a new one."
            : "Create a key for a service account with BigQuery Data Viewer and BigQuery Job User roles."}
        </p>
        <input type="file" accept="application/json,.json" aria-label="Upload key file" onChange={(e) => onFile(e.target.files?.[0])} />
        {fileKey ? (
          <p className="ok" role="status">{fileKey.label}</p>
        ) : (
          <textarea
            rows={4}
            value={d.credentials_json}
            onChange={set("credentials_json")}
            placeholder='…or paste the key: {"type": "service_account", ...}'
            aria-label="Service-account key JSON"
            spellCheck={false}
            autoComplete="off"
          />
        )}
      </fieldset>
      <div className="settings-grid">
        <label>
          Project ID
          <input value={d.project} onChange={set("project")} placeholder="defaults to the key's project" />
        </label>
        <label>
          Default dataset (optional)
          <input value={d.default_dataset} onChange={set("default_dataset")} />
        </label>
        <label>
          Location (optional)
          <input value={d.location} onChange={set("location")} placeholder="e.g. US" />
        </label>
        <label>
          Billing project (optional)
          <input value={d.billing_project} onChange={set("billing_project")} placeholder="defaults to the project" />
        </label>
      </div>
      <label htmlFor={`${ids}-allowed`}>Datasets/tables you can query (optional)</label>
      <textarea
        id={`${ids}-allowed`}
        rows={3}
        value={d.allowed}
        onChange={set("allowed")}
        placeholder={"One per line: dataset, dataset.table or project.dataset.*\nEmpty = every dataset the key can read in the project"}
        spellCheck={false}
      />
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

function ConnectionsSection() {
  const ids = useId();
  const [connections, setConnections] = useState<Connection[]>([]);
  const [editing, setEditing] = useState<{ kind: string; draft: any } | null>(null);
  const [showKindPicker, setShowKindPicker] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api<Connection[]>("connections").then(setConnections).catch((e: Error) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  async function remove(c: Connection) {
    if (!window.confirm(`Delete connection "${c.name}"? Its saved key is deleted too.`)) return;
    try {
      await api(`me/connections/${c.id}`, { method: "DELETE" });
      notify(CONNECTIONS_CHANGED);
      load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  function startEdit(c: Connection) {
    if (c.kind === "postgres") {
      setEditing({ kind: "postgres", draft: draftOfPostgres(c) });
    } else {
      setEditing({ kind: "bigquery", draft: draftOf(c) });
    }
  }

  function startNew(kind: string) {
    if (kind === "postgres") {
      setEditing({ kind: "postgres", draft: EMPTY_POSTGRES_DRAFT });
    } else {
      setEditing({ kind: "bigquery", draft: EMPTY });
    }
    setShowKindPicker(false);
  }

  const shared = connections.filter((c) => c.scope === "shared");
  const personal = connections.filter((c) => c.scope === "personal");

  const connectionDisplay = (c: Connection) => {
    if (c.kind === "postgres") {
      return (
        <>
          <strong>{c.name}</strong> <span className="badge">PostgreSQL</span>{" "}
          <span className="muted">
            {c.config.host}:{c.config.port}/{c.config.database} · {c.allowed_objects.length} allowlist entr
            {c.allowed_objects.length === 1 ? "y" : "ies"}
          </span>
        </>
      );
    } else {
      return (
        <>
          <strong>{c.name}</strong> <span className="badge">BigQuery</span>{" "}
          <span className="muted">{c.config.project} · {c.allowed_objects.length} allowlist entr{c.allowed_objects.length === 1 ? "y" : "ies"}</span>
        </>
      );
    }
  };

  return (
    <section className="settings-card" aria-labelledby={`${ids}-h`}>
      <h2 id={`${ids}-h`}>Data connections</h2>
      <p className="muted">
        Connect your own BigQuery or PostgreSQL databases. Your connections and keys are visible only to you; queries
        still go through read-only checks and cost limits.
      </p>
      {shared.length > 0 && (
        <>
          <h3>Shared by your organization</h3>
          <ul className="connection-list">
            {shared.map((c) => (
              <li key={c.id}>{connectionDisplay(c)}</li>
            ))}
          </ul>
        </>
      )}
      <h3>Your connections</h3>
      {personal.length === 0 && !editing && <p className="muted">You haven&apos;t added any connections yet.</p>}
      <ul className="connection-list">
        {personal.map((c) => (
          <li key={c.id}>
            {connectionDisplay(c)}{" "}
            <button type="button" className="link" onClick={() => startEdit(c)}>
              Edit
            </button>{" "}
            <button type="button" className="link danger" onClick={() => remove(c)}>
              Delete
            </button>
          </li>
        ))}
      </ul>
      {editing ? (
        editing.kind === "postgres" ? (
          <PostgresConnectionForm
            key={editing.draft.id ?? "new"}
            initial={editing.draft}
            onDone={(saved) => {
              setEditing(null);
              if (saved) load();
            }}
          />
        ) : (
          <ConnectionForm
            key={editing.draft.id ?? "new"}
            initial={editing.draft}
            onDone={(saved) => {
              setEditing(null);
              if (saved) load();
            }}
          />
        )
      ) : showKindPicker ? (
        <div className="connection-kind-picker">
          <p>Select a data source type:</p>
          <div className="settings-actions">
            <button type="button" onClick={() => startNew("bigquery")}>
              BigQuery
            </button>
            <button type="button" onClick={() => startNew("postgres")}>
              PostgreSQL
            </button>
            <button type="button" onClick={() => setShowKindPicker(false)}>
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <button type="button" onClick={() => setShowKindPicker(true)}>
          + Add data source
        </button>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  );
}

export default function Settings() {
  return (
    <div className="settings">
      <LlmKeySection />
      <ConnectionsSection />
    </div>
  );
}
