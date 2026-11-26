"use client";

import { useCallback, useEffect, useId, useState, type FormEvent, type ReactNode } from "react";

import { api, CONNECTIONS_CHANGED, LLM_CHANGED, notify, type Budget, type Connection } from "@/lib/client-api";
import type { CollibraEnv } from "@/lib/collibra";
import CollibraSettings from "./CollibraSettings";
import PostgresConnectionForm, { EMPTY_POSTGRES_DRAFT, draftOfPostgres } from "./PostgresConnectionForm";
import SnowflakeConnectionForm, { EMPTY_SNOWFLAKE_DRAFT, draftOfSnowflake } from "./SnowflakeConnectionForm";
import MySQLConnectionForm, { EMPTY_MYSQL_DRAFT, draftOfMySQL } from "./MySQLConnectionForm";
import OracleConnectionForm, { EMPTY_ORACLE_DRAFT, draftOfOracle } from "./OracleConnectionForm";
import { DB_LOGOS } from "./dbLogos";

// ---------------------------------------------------------------------------------------------
// AI model (bring your own key)

type Provider = "gemini" | "openai" | "anthropic" | "openai_compatible" | "github_copilot";

const PROVIDERS: { id: Provider; label: string; keyHelp: string; modelHint: string }[] = [
  { id: "github_copilot", label: "GitHub Copilot (sign in with GitHub)", keyHelp: "", modelHint: "e.g. claude-sonnet-5.5" },
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

// Provider model lists also include embedding, audio, image and moderation models; the chat can only use chat models.
const NON_CHAT_MODEL = /(embed|tts|whisper|dall-e|moderation|davinci|babbage|image|imagen|audio|realtime|transcri|search|computer-use|aqa|veo|lyria|banana|robotics)/i;
const OTHER_MODEL = "__other__";

type ModelList = { key: string; state: "loading" | "ready" | "error"; models: string[]; error?: string | null };

type LlmKey = { provider: Provider; model: string; api_base: string | null; key_hint: string; updated_at: string };
type LlmTest = { ok: boolean; error: string | null; models: string[]; model_found: boolean | null };

function ModelStatus({ budget, current, onRemove, busy }: { budget: Budget | null; current: LlmKey | null; onRemove: () => void; busy: boolean }) {
  if (!budget) return null;
  if (budget.key_source === "user" && current) {
    const label = PROVIDERS.find((p) => p.id === current.provider)?.label ?? current.provider;
    return (
      <div className="settings-status settings-status--ok">
        <div>
          {current.provider === "github_copilot" ? (
            <>
              <strong>GitHub Copilot is active</strong>
              <p className="muted">
                Signed in as <strong>{current.key_hint}</strong> · <code>{current.model}</code>. Uses your Copilot seat and your
                organization&apos;s Copilot plan; no platform limit.
              </p>
            </>
          ) : (
            <>
              <strong>Your key is active</strong>
              <p className="muted">
                {label} · <code>{current.model}</code> · key {current.key_hint}. Usage is billed by your provider; no platform limit.
              </p>
            </>
          )}
        </div>
        <button type="button" className="odn-btn odn-btn--outline odn-btn--small" onClick={onRemove} disabled={busy}>
          {current.provider === "github_copilot" ? "Sign out" : "Remove key"}
        </button>
      </div>
    );
  }
  const used = budget.user_tokens_used_today ?? 0;
  const limit = budget.user_daily_token_limit ?? 0;
  const pct = limit > 0 ? Math.min(100, Math.round((used / limit) * 100)) : 100;
  return (
    <div className={`settings-status${budget.exceeded ? " settings-status--warn" : ""}`}>
      <div className="settings-status-main">
        <strong>{budget.exceeded ? "Today's free trial is used up" : "Free trial on the shared model"}</strong>
        {limit > 0 ? (
          <>
            <div className="settings-meter" role="meter" aria-valuemin={0} aria-valuemax={limit} aria-valuenow={used} aria-label="Trial tokens used today">
              <span style={{ width: `${pct}%` }} />
            </div>
            <p className="muted">{used.toLocaleString()} of {limit.toLocaleString()} tokens used today. Add your own key below to chat without limits.</p>
          </>
        ) : (
          <p className="muted">No free trial is available. Add your own key below to chat.</p>
        )}
      </div>
    </div>
  );
}

type DeviceCode = { device_code: string; user_code: string; verification_uri: string; expires_in: number; interval: number };
type DevicePoll = { status: "pending" | "slow_down" | "expired" | "denied" | "connected"; interval?: number | null; login?: string | null };

/** GitHub device-flow sign-in (like DBeaver or the GitHub CLI): show a code, the user approves it on github.com. */
function GitHubSignIn({ signedInAs, onConnected }: { signedInAs: string | null; onConnected: () => void }) {
  const [flow, setFlow] = useState<DeviceCode | null>(null);
  const [state, setState] = useState<"idle" | "starting" | "waiting" | "error">("idle");
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  async function start() {
    setState("starting");
    setError(null);
    try {
      setFlow(await api<DeviceCode>("me/llm-key/github/start", { method: "POST" }));
      setState("waiting");
    } catch (e) {
      setError((e as Error).message);
      setState("error");
    }
  }

  // Poll at GitHub's pace until the user approves, declines, or the code expires.
  useEffect(() => {
    if (state !== "waiting" || !flow) return;
    let cancelled = false;
    let delay = flow.interval * 1000;
    let timer: ReturnType<typeof setTimeout>;
    const deadline = Date.now() + flow.expires_in * 1000;
    const poll = async () => {
      if (cancelled) return;
      if (Date.now() > deadline) {
        setError("The code expired. Start again.");
        setState("error");
        return;
      }
      try {
        const r = await api<DevicePoll>("me/llm-key/github/poll", {
          method: "POST",
          body: JSON.stringify({ device_code: flow.device_code }),
        });
        if (cancelled) return;
        if (r.status === "connected") {
          setFlow(null);
          setState("idle");
          onConnected();
          return;
        }
        if (r.status === "denied" || r.status === "expired") {
          setError(r.status === "denied" ? "Sign-in was declined on GitHub." : "The code expired. Start again.");
          setState("error");
          return;
        }
        if (r.status === "slow_down") delay = (r.interval ?? flow.interval + 5) * 1000;
      } catch (e) {
        if (cancelled) return;
        setError((e as Error).message);
        setState("error");
        return;
      }
      timer = setTimeout(poll, delay);
    };
    timer = setTimeout(poll, delay);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [state, flow, onConnected]);

  if (state === "waiting" && flow) {
    return (
      <div className="gh-device" role="group" aria-label="Sign in with GitHub">
        <p><strong>1.</strong> Copy this one-time code</p>
        <div className="gh-device-code">
          <code aria-label="One-time code">{flow.user_code}</code>
          <button
            type="button"
            className="odn-btn odn-btn--outline odn-btn--small"
            onClick={() => {
              void navigator.clipboard?.writeText(flow.user_code);
              setCopied(true);
            }}
          >
            {copied ? "Copied" : "Copy"}
          </button>
        </div>
        <p><strong>2.</strong> Paste it on GitHub and authorize EasyQuery</p>
        <a className="odn-btn odn-btn--small" href={flow.verification_uri} target="_blank" rel="noreferrer">
          Open {flow.verification_uri.replace(/^https:\/\//, "")} ↗
        </a>
        <p className="muted gh-device-wait" role="status">
          <span className="gh-spinner" aria-hidden /> Waiting for you to approve on GitHub…{" "}
          <button type="button" className="odn-linklike" onClick={() => { setFlow(null); setState("idle"); }}>Cancel</button>
        </p>
      </div>
    );
  }

  return (
    <div className="gh-signin">
      {signedInAs ? (
        <p className="muted">
          Signed in as <strong>{signedInAs}</strong>.{" "}
          <button type="button" className="odn-linklike" onClick={start} disabled={state === "starting"}>Use another GitHub account</button>
        </p>
      ) : (
        <>
          <button type="button" className="gh-signin-btn" onClick={start} disabled={state === "starting"}>
            <svg aria-hidden viewBox="0 0 16 16" width="18" height="18" fill="currentColor">
              <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z" />
            </svg>
            {state === "starting" ? "Starting…" : "Sign in with GitHub"}
          </button>
          <p className="muted settings-hint">Uses your GitHub Copilot seat. EasyQuery only reads your GitHub username.</p>
        </>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </div>
  );
}

function LlmKeySection({ onSummary }: { onSummary?: (s: string) => void }) {
  const ids = useId();
  const [current, setCurrent] = useState<LlmKey | null>(null);
  const [budget, setBudget] = useState<Budget | null>(null);
  const [provider, setProvider] = useState<Provider>("gemini");
  const [model, setModel] = useState("");
  const [apiBase, setApiBase] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [list, setList] = useState<ModelList | null>(null);
  const [manual, setManual] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [githubEnabled, setGithubEnabled] = useState(false);

  const load = useCallback(async () => {
    const [key, b, gh] = await Promise.all([
      api<LlmKey | null>("me/llm-key"),
      api<Budget>("llm-budget"),
      api<{ enabled: boolean }>("me/llm-key/github").catch(() => ({ enabled: false })),
    ]);
    setCurrent(key);
    setBudget(b);
    setGithubEnabled(gh.enabled);
    // Most teams already have GitHub Copilot: offer it first when nothing is set up yet.
    if (!key && gh.enabled) setProvider("github_copilot");
    if (key) {
      setProvider(key.provider);
      setModel(key.model);
      setApiBase(key.api_base ?? "");
    }
  }, []);

  useEffect(() => {
    load().catch((e: Error) => setMessage({ kind: "error", text: e.message }));
  }, [load]);

  useEffect(() => {
    if (budget)
      onSummary?.(
        budget.key_source === "user"
          ? current?.provider === "github_copilot" ? "GitHub Copilot" : "Your key"
          : budget.exceeded ? "Trial used up" : "Free trial",
      );
  }, [budget, current, onSummary]);

  const info = PROVIDERS.find((p) => p.id === provider)!;
  const sameProvider = current?.provider === provider && (provider !== "openai_compatible" || current.api_base === apiBase.replace(/\/+$/, ""));
  const body = () => JSON.stringify({ provider, model, api_base: provider === "openai_compatible" ? apiBase : null, api_key: apiKey || null });

  // The provider lists models only for a valid key: load them as soon as there is one (typed, or already saved).
  const canList = provider !== "openai_compatible" || /^https?:\/\/\S+/.test(apiBase);
  const listKey = canList && (apiKey.trim().length >= 8 || sameProvider) ? `${provider}|${apiBase}|${apiKey.trim()}` : "";
  useEffect(() => {
    if (!listKey) return;
    let cancelled = false;
    const timer = setTimeout(async () => {
      setList({ key: listKey, state: "loading", models: [] });
      try {
        const r = await api<LlmTest>("me/llm-key/test", {
          method: "POST",
          body: JSON.stringify({ provider, api_base: provider === "openai_compatible" ? apiBase : null, api_key: apiKey.trim() || null }),
        });
        if (cancelled) return;
        setList(
          r.ok
            ? { key: listKey, state: "ready", models: r.models.filter((m) => !NON_CHAT_MODEL.test(m)) }
            : { key: listKey, state: "error", models: [], error: r.error },
        );
      } catch (e) {
        if (!cancelled) setList({ key: listKey, state: "error", models: [], error: (e as Error).message });
      }
    }, apiKey ? 600 : 0);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
    // listKey covers provider, endpoint and key.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [listKey]);
  const models = listKey && list?.key === listKey ? list : null;
  const pickFromList = !manual && models?.state === "ready" && models.models.length > 0;

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
    const what = current?.provider === "github_copilot" ? "Sign out of GitHub Copilot" : "Remove your API key";
    if (!window.confirm(`${what}? Chat falls back to the shared trial model.`)) return;
    setBusy(true);
    setMessage(null);
    try {
      await api<void>("me/llm-key", { method: "DELETE" });
      setCurrent(null);
      setApiKey("");
      await load();
      notify(LLM_CHANGED);
      setMessage({ kind: "ok", text: "Key removed." });
    } catch (e) {
      setMessage({ kind: "error", text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  }

  const onGitHubConnected = useCallback(() => {
    load()
      .then(() => {
        notify(LLM_CHANGED);
        setMessage({ kind: "ok", text: "Signed in with GitHub. The chat now uses your Copilot seat; pick a model below." });
      })
      .catch((e: Error) => setMessage({ kind: "error", text: e.message }));
  }, [load]);

  return (
    <section className="settings-card" aria-labelledby={`${ids}-h`}>
      <header className="settings-head">
        <h2 id={`${ids}-h`}>AI model</h2>
        <p className="muted">The model behind the chat assistant.</p>
      </header>
      <ModelStatus budget={budget} current={current} onRemove={onRemove} busy={busy} />
      <h3>{current ? "Change model or provider" : "Use your own model"}</h3>
      <p className="muted">
        Sign in with GitHub to use your Copilot seat, or bring an API key. Credentials are encrypted at rest and only used
        for your requests.
      </p>
      <form onSubmit={onSave} className="settings-form">
        <label>
          Provider
          <select
            value={provider}
            onChange={(e) => {
              const next = e.target.value as Provider;
              setProvider(next);
              setModel(current?.provider === next ? current.model : "");
              setManual(false);
            }}
          >
            {PROVIDERS.filter((p) => p.id !== "github_copilot" || githubEnabled || current?.provider === p.id).map((p) => (
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
        {provider === "github_copilot" ? (
          <GitHubSignIn signedInAs={sameProvider ? current?.key_hint ?? null : null} onConnected={onGitHubConnected} />
        ) : (
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
        )}
        <label>
          Model
          {pickFromList ? (
            <select
              required
              value={model}
              onChange={(e) => (e.target.value === OTHER_MODEL ? setManual(true) : setModel(e.target.value))}
            >
              {!model && <option value="">Choose a model…</option>}
              {model && !models.models.includes(model) && <option value={model}>{model} (not in your key&apos;s list)</option>}
              {models.models.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
              <option value={OTHER_MODEL}>Other — type a model name…</option>
            </select>
          ) : manual || models?.state === "error" || (models?.state === "ready" && models.models.length === 0) ? (
            <input required value={model} onChange={(e) => setModel(e.target.value)} placeholder={info.modelHint} autoComplete="off" />
          ) : (
            <select disabled value="">
              <option value="">
                {listKey ? "Loading models…" : provider === "github_copilot" ? "Sign in with GitHub to load models" : "Enter your API key to load models"}
              </option>
            </select>
          )}
        </label>
        <p className={`settings-hint${models?.state === "error" ? " error" : ""}`} role="status">
          {models?.state === "ready" && models.models.length > 0 && (
            <>
              {provider === "github_copilot" ? "Copilot works" : "Key works"} · {models.models.length} chat models available.{" "}
              <button type="button" className="odn-linklike" onClick={() => setManual(!manual)}>
                {manual ? "Choose from the list" : "Type a model name instead"}
              </button>
            </>
          )}
          {models?.state === "ready" && models.models.length === 0 && "Key works, but the provider listed no models. Type the model name."}
          {models?.state === "error" && `Couldn't load models: ${models.error}. Check the key, or type the model name.`}
          {!models && listKey && "Checking the key and loading models…"}
          {!models && !listKey && !manual && provider !== "github_copilot" && (
            <>
              Models appear once the key is entered.{" "}
              <button type="button" className="odn-linklike" onClick={() => setManual(true)}>Type a model name instead</button>
            </>
          )}
        </p>
        <div className="settings-actions">
          <button type="submit" disabled={busy || !model || models?.state === "loading"}>Save</button>
        </div>
      </form>
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

function ConnectionForm({
  initial,
  onDone,
  base = "me/connections",
}: {
  initial: Draft;
  onDone: (saved: boolean) => void;
  base?: string;
}) {
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

type EditingConnection = (
  | { kind: "bigquery"; draft: Draft }
  | { kind: "postgres"; draft: ReturnType<typeof draftOfPostgres> }
  | { kind: "snowflake"; draft: ReturnType<typeof draftOfSnowflake> }
  | { kind: "mysql"; draft: ReturnType<typeof draftOfMySQL> }
  | { kind: "oracle"; draft: ReturnType<typeof draftOfOracle> }
) & { shared: boolean };

const KIND_LABEL: Record<string, string> = { bigquery: "BigQuery", postgres: "PostgreSQL", snowflake: "Snowflake", mysql: "MySQL", oracle: "Oracle" };
const KIND_ORDER = ["bigquery", "snowflake", "oracle", "postgres", "mysql"];

function SourceLogo({ kind, size = "sm" }: { kind: string; size?: "sm" | "lg" }) {
  const logo = DB_LOGOS.find((d) => d.id === kind);
  return (
    <span className={`settings-logo settings-logo--${size}`} aria-hidden>
      {logo?.path ? (
        <svg viewBox={logo.viewBox} fill={logo.color} focusable="false"><path d={logo.path} /></svg>
      ) : (
        <span>{(KIND_LABEL[kind] ?? kind).slice(0, 2)}</span>
      )}
    </span>
  );
}

function connectionWhere(c: Connection): string {
  if (c.kind === "postgres" || c.kind === "mysql") return `${c.config.host}:${c.config.port}/${c.config.database}`;
  if (c.kind === "oracle") return `${c.config.host}:${c.config.port}/${c.config.service_name}`;
  if (c.kind === "snowflake") return c.config.account ?? "";
  return c.config.project ?? "";
}

function ConnectionsSection({ canManageShared = false, onSummary }: { canManageShared?: boolean; onSummary?: (s: string) => void }) {
  const ids = useId();
  const [connections, setConnections] = useState<Connection[]>([]);
  const [editing, setEditing] = useState<EditingConnection | null>(null);
  const [showKindPicker, setShowKindPicker] = useState(false);
  const [shareNew, setShareNew] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api<Connection[]>("connections").then(setConnections).catch((e: Error) => setError(e.message));
  }, []);
  useEffect(load, [load]);
  useEffect(() => {
    onSummary?.(connections.length ? `${connections.length} connected` : "None yet");
  }, [connections, onSummary]);

  const baseOf = (shared: boolean) => (shared ? "connections/shared" : "me/connections");

  async function remove(c: Connection) {
    const who = c.scope === "shared" ? " It is removed for everyone in your organization." : "";
    if (!window.confirm(`Delete connection "${c.name}"? Its saved key is deleted too.${who}`)) return;
    try {
      await api(`${baseOf(c.scope === "shared")}/${c.id}`, { method: "DELETE" });
      notify(CONNECTIONS_CHANGED);
      load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  function startEdit(c: Connection) {
    const shared = c.scope === "shared";
    if (c.kind === "postgres") {
      setEditing({ kind: "postgres", draft: draftOfPostgres(c), shared });
    } else if (c.kind === "snowflake") {
      setEditing({ kind: "snowflake", draft: draftOfSnowflake(c), shared });
    } else if (c.kind === "mysql") {
      setEditing({ kind: "mysql", draft: draftOfMySQL(c), shared });
    } else if (c.kind === "oracle") {
      setEditing({ kind: "oracle", draft: draftOfOracle(c), shared });
    } else {
      setEditing({ kind: "bigquery", draft: draftOf(c), shared });
    }
  }

  function startNew(kind: string) {
    const shared = canManageShared && shareNew;
    if (kind === "postgres") {
      setEditing({ kind: "postgres", draft: EMPTY_POSTGRES_DRAFT, shared });
    } else if (kind === "snowflake") {
      setEditing({ kind: "snowflake", draft: EMPTY_SNOWFLAKE_DRAFT, shared });
    } else if (kind === "mysql") {
      setEditing({ kind: "mysql", draft: EMPTY_MYSQL_DRAFT, shared });
    } else if (kind === "oracle") {
      setEditing({ kind: "oracle", draft: EMPTY_ORACLE_DRAFT, shared });
    } else {
      setEditing({ kind: "bigquery", draft: EMPTY, shared });
    }
    setShowKindPicker(false);
  }

  const shared = connections.filter((c) => c.scope === "shared");
  const personal = connections.filter((c) => c.scope === "personal");
  const done = (saved: boolean) => {
    setEditing(null);
    if (saved) load();
  };

  const row = (c: Connection, canManage: boolean) => (
    <li key={c.id} className="settings-row">
      <SourceLogo kind={c.kind} />
      <div className="settings-row-main">
        <strong>{c.name}</strong>
        <span className="muted" title={`${KIND_LABEL[c.kind] ?? c.kind} · ${connectionWhere(c)} · ${c.allowed_objects.length} allowlisted`}>
          {KIND_LABEL[c.kind] ?? c.kind} · {connectionWhere(c)} · {c.allowed_objects.length} allowlisted
        </span>
      </div>
      {canManage && (
        <div className="settings-row-actions">
          <button type="button" className="odn-btn odn-btn--outline odn-btn--small" onClick={() => startEdit(c)}>Edit</button>
          <button
            type="button"
            className="odn-btn odn-btn--outline odn-btn--small settings-danger"
            onClick={() => remove(c)}
            aria-label={`Delete ${c.name}`}
          >
            Delete
          </button>
        </div>
      )}
    </li>
  );

  if (editing) {
    const formProps = { base: baseOf(editing.shared), onDone: done };
    const formKey = editing.draft.id ?? "new";
    return (
      <section className="settings-card" aria-labelledby={`${ids}-h`}>
        <button type="button" className="settings-back" onClick={() => setEditing(null)}>← All data sources</button>
        <header className="settings-head settings-head--with-logo">
          <SourceLogo kind={editing.kind} size="lg" />
          <div>
            <h2 id={`${ids}-h`}>
              {editing.draft.id ? `Edit ${editing.draft.name}` : `New ${KIND_LABEL[editing.kind]} connection`}
            </h2>
            <p className="muted">
              {editing.shared ? "Shared with everyone in your organization." : "Only you can see and use it."} Queries are read-only and
              limited to the allowlist.
            </p>
          </div>
        </header>
        {editing.kind === "postgres" ? (
          <PostgresConnectionForm key={formKey} {...formProps} initial={editing.draft} />
        ) : editing.kind === "snowflake" ? (
          <SnowflakeConnectionForm key={formKey} {...formProps} initial={editing.draft} />
        ) : editing.kind === "mysql" ? (
          <MySQLConnectionForm key={formKey} {...formProps} initial={editing.draft} />
        ) : editing.kind === "oracle" ? (
          <OracleConnectionForm key={formKey} {...formProps} initial={editing.draft} />
        ) : (
          <ConnectionForm key={formKey} {...formProps} initial={editing.draft} />
        )}
      </section>
    );
  }

  return (
    <section className="settings-card" aria-labelledby={`${ids}-h`}>
      <header className="settings-head settings-head--action">
        <div>
          <h2 id={`${ids}-h`}>Data sources</h2>
          <p className="muted">Warehouses and databases the assistant can read. Every query is read-only and allowlisted.</p>
        </div>
        {!showKindPicker && (
          <button type="button" className="odn-btn odn-btn--small" onClick={() => setShowKindPicker(true)}>+ Add</button>
        )}
      </header>

      {showKindPicker && (
        <div className="settings-picker" role="group" aria-label="Choose a data source type">
          <div className="settings-picker-head">
            <strong>Add a data source</strong>
            <button type="button" className="odn-btn odn-btn--outline odn-btn--small" onClick={() => setShowKindPicker(false)}>Cancel</button>
          </div>
          <div className="settings-picker-grid">
            {KIND_ORDER.map((k) => (
              <button key={k} type="button" className="settings-picker-tile" onClick={() => startNew(k)}>
                <SourceLogo kind={k} size="lg" />
                {KIND_LABEL[k]}
              </button>
            ))}
          </div>
          {canManageShared && (
            <label className="connection-share">
              <input type="checkbox" checked={shareNew} onChange={(e) => setShareNew(e.target.checked)} /> Share with everyone in my
              organization
            </label>
          )}
        </div>
      )}

      {(shared.length > 0 || canManageShared) && (
        <>
          <h3>Shared with your organization</h3>
          {shared.length === 0 ? (
            <p className="muted settings-empty">No shared data sources yet.</p>
          ) : (
            <ul className="settings-rows">{shared.map((c) => row(c, canManageShared))}</ul>
          )}
        </>
      )}
      <h3>Only you</h3>
      {personal.length === 0 ? (
        <p className="muted settings-empty">
          You haven&apos;t added your own data sources. Use <strong>+ Add</strong> to connect BigQuery, Snowflake, Oracle, PostgreSQL or
          MySQL; only you will see it.
        </p>
      ) : (
        <ul className="settings-rows">{personal.map((c) => row(c, true))}</ul>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  );
}

type SectionId = "model" | "sources" | "collibra";
const SECTION_KEY = "odn.settings.section";
/** Dispatch with `detail: SectionId` to open Settings on a section (e.g. the trial banner opens "model"). */
export const SETTINGS_SECTION = "odn:settings-section";

const SECTION_ICONS: Record<SectionId, ReactNode> = {
  model: <path d="M12 3l1.8 4.7L18.5 9.5l-4.7 1.8L12 16l-1.8-4.7L5.5 9.5l4.7-1.8zM18 15l.8 2.2L21 18l-2.2.8L18 21l-.8-2.2L15 18l2.2-.8z" />,
  sources: <path d="M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3zM4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6" />,
  collibra: <path d="M5 4h11a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3zM5 17a3 3 0 0 1 3-3h11M9 8h6" />,
};

/**
 * Settings: one section at a time behind a switcher that shows each section's status, so the whole setup reads
 * at a glance in the narrow side panel. Sections stay mounted (hidden) to keep form state when switching.
 */
export default function Settings({ role, collibraEnvs = null }: { role?: string; collibraEnvs?: CollibraEnv[] | null }) {
  const isAdmin = role === "admin";
  const showCollibra = isAdmin || (collibraEnvs?.length ?? 0) > 0;
  // Settings only renders client-side (AppShell waits for a thread id), so localStorage is available here.
  const [section, setSection] = useState<SectionId>(() => {
    const saved = localStorage.getItem(SECTION_KEY);
    return saved === "sources" || saved === "collibra" ? saved : "model";
  });
  const [summary, setSummary] = useState<Record<string, string>>({});
  const onModel = useCallback((v: string) => setSummary((s) => ({ ...s, model: v })), []);
  const onSources = useCallback((v: string) => setSummary((s) => ({ ...s, sources: v })), []);

  const choose = useCallback((id: SectionId) => {
    setSection(id);
    localStorage.setItem(SECTION_KEY, id);
  }, []);

  useEffect(() => {
    const onRequest = (e: Event) => choose((e as CustomEvent<SectionId>).detail);
    window.addEventListener(SETTINGS_SECTION, onRequest);
    return () => window.removeEventListener(SETTINGS_SECTION, onRequest);
  }, [choose]);

  const sections: { id: SectionId; label: string; status: string }[] = [
    { id: "model", label: "AI model", status: summary.model ?? "…" },
    { id: "sources", label: "Data sources", status: summary.sources ?? "…" },
    ...(showCollibra
      ? [{ id: "collibra" as const, label: "Collibra", status: collibraEnvs?.length ? `${collibraEnvs.length} connected` : "Not set up" }]
      : []),
  ];
  const active = sections.some((x) => x.id === section) ? section : "model";

  return (
    <div className="settings settings--sectioned">
      <nav className="settings-nav" aria-label="Settings sections">
        {sections.map((x) => (
          <button
            key={x.id}
            type="button"
            className="settings-nav-item"
            aria-current={active === x.id ? "page" : undefined}
            onClick={() => choose(x.id)}
          >
            <svg aria-hidden viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              {SECTION_ICONS[x.id]}
            </svg>
            <span className="settings-nav-text">
              <span className="settings-nav-label">{x.label}</span>
              <span className="settings-nav-status">{x.status}</span>
            </span>
          </button>
        ))}
      </nav>
      <div hidden={active !== "model"}>
        <LlmKeySection onSummary={onModel} />
      </div>
      <div hidden={active !== "sources"}>
        <ConnectionsSection canManageShared={isAdmin || role === "data_lead"} onSummary={onSources} />
      </div>
      {showCollibra && (
        <div hidden={active !== "collibra"}>
          <CollibraSettings envs={collibraEnvs} isAdmin={isAdmin} />
        </div>
      )}
    </div>
  );
}
