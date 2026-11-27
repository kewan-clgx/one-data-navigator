"use client";

import { CopilotKit, useCoAgent, useCopilotReadable } from "@copilotkit/react-core";
import { useAgent } from "@copilotkit/react-core/v2";
import { CopilotChat } from "@copilotkit/react-ui";
import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";

import { api, LLM_CHANGED, type Budget } from "@/lib/client-api";
import { CHAT_ATTACH_ACCEPT, FILES_CHANGED, UPLOAD_MAX_BYTES, uploadChatAttachment } from "@/lib/uploads";

import type { Theme } from "../themes";
import AboutSlides from "./AboutSlides";
import { SiteContext, type SiteInfo } from "@/lib/site-context";
import { AgentStepsToggle, ChatSessionContext, ShowResultsContext, useAgentStepRenderers } from "./AgentSteps";
import { CollibraBrowserTools, type CollibraScope } from "./CollibraBrowserTools";
import CollibraPanel from "./CollibraPanel";
import { useCollibraBridge, useCollibraEnvironments } from "@/lib/collibra";
import ArtifactsPanel from "./ArtifactsPanel";
import ChatsPanel, { useConversations } from "./Conversations";
import Explorer, { type ActiveConnection, type SqlRequest, type TableScope } from "./Explorer";
import FoldableSql, { OpenSqlContext } from "./FoldableSql";
import ResultsPanel, { AGENT_ID, type AgentState } from "./Results";
import { DEMO_PLACEHOLDER, DEMO_SUGGESTIONS, DemoImportBanner, DemoUpgradeBanner, type DemoVisitor } from "./DemoBanners";
import ShareDialog from "./ShareDialog";
import Organization from "./Organization";
import Settings, { SETTINGS_SECTION } from "./Settings";
import SplitPane from "./SplitPane";

const TABS = [
  { id: "about", label: "About" },
  { id: "chats", label: "Chats" },
  { id: "explorer", label: "Explorer" },
  { id: "collibra", label: "Collibra" },
  { id: "results", label: "Results" },
  { id: "artifacts", label: "Artifacts" },
  { id: "settings", label: "Settings" },
  { id: "organization", label: "Organization" },
] as const;

type TabId = (typeof TABS)[number]["id"];

const PANEL_OPEN_KEY = "odn.panelOpen";
// Workbench "Focus": hide the side panel while the workbench is open (user's choice, remembered; off by default).
const WORKBENCH_FOCUS_KEY = "odn.workbench.focus";
const THREAD_KEY = "odn.activeThreadId";
// CopilotKit's dev-only Inspector button is fixed top-right; keep the chat toolbar clear of it locally.
const IS_DEV = process.env.NODE_ENV === "development";

/** Prompts users on the shared trial model to add their own LLM key. */
function TrialBanner({ onOpenSettings }: { onOpenSettings: () => void }) {
  const [budget, setBudget] = useState<Budget | null>(null);
  const load = useCallback(() => {
    api<Budget>("llm-budget").then(setBudget).catch(() => setBudget(null));
  }, []);
  useEffect(() => {
    load();
    window.addEventListener(LLM_CHANGED, load);
    return () => window.removeEventListener(LLM_CHANGED, load);
  }, [load]);

  if (!budget || budget.key_source === "user") return null;
  const used = budget.user_tokens_used_today ?? 0;
  const limit = budget.user_daily_token_limit ?? 0;
  return (
    <div className={`trial-banner${budget.exceeded ? " exceeded" : ""}`} role="status">
      {budget.exceeded
        ? "Today's free trial of the shared AI model is used up."
        : `You're on the free trial of the shared AI model (${used.toLocaleString()} of ${limit.toLocaleString()} tokens used today).`}{" "}
      <button type="button" className="link" onClick={onOpenSettings}>
        Add your own API key
      </button>{" "}
      to keep chatting without limits.
    </div>
  );
}

/** Loads the conversation's saved messages and state into the chat whenever the thread changes. */
function HistoryHydrator({ threadId }: { threadId: string }) {
  const { agent } = useAgent({ agentId: AGENT_ID });
  useEffect(() => {
    if (!agent) return;
    let cancelled = false;
    agent.setMessages([]);
    agent.setState({});
    fetch("/api/agent/thread-state", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ threadId }),
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (cancelled || !data?.threadExists) return;
        if (Array.isArray(data.messages) && data.messages.length) agent.setMessages(data.messages);
        if (data.state && typeof data.state === "object") {
          // Internal bridge keys (thread id, shared context) stay server-side.
          agent.setState(Object.fromEntries(Object.entries(data.state).filter(([k]) => !k.startsWith("_"))));
        }
      })
      .catch(() => {
        // Best effort: the conversation still continues with new messages.
      });
    return () => {
      cancelled = true;
    };
  }, [agent, threadId]);
  return null;
}

/**
 * Calls `onNew` when the agent publishes a new result or chart during a run (not when a saved
 * conversation is loaded), and `onRunEnd` when a run finishes (titles and artifacts may have changed).
 */
function RunWatcher({ onNew, onRunEnd }: { onNew: () => void; onRunEnd: () => void }) {
  const { state, running } = useCoAgent<AgentState>({ name: AGENT_ID, initialState: {} });
  const { last_result, last_chart } = state ?? {};
  const key = last_result || last_chart
    ? `${last_result?.session_id}/${last_result?.name}|${last_chart ? JSON.stringify(last_chart.spec) + last_chart.points.length : ""}`
    : "";
  const previous = useRef(key);
  const wasRunning = useRef(running);
  useEffect(() => {
    if (running && key && key !== previous.current) onNew();
    previous.current = key;
  }, [key, running, onNew]);
  useEffect(() => {
    if (wasRunning.current && !running) onRunEnd();
    wasRunning.current = running;
  }, [running, onRunEnd]);
  return null;
}

type UploadedFile = { name: string; table_name: string | null; mime_type: string };

/** Tells the agent which files were uploaded in this conversation and which table each became. */
function UploadsContext({ sessionId, revision }: { sessionId: string; revision: number }) {
  const [files, setFiles] = useState<UploadedFile[]>([]);
  const load = useCallback(() => {
    fetch(`/api/odn/artifacts?kind=file&session_id=${encodeURIComponent(sessionId)}`, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : []))
      .then((items: UploadedFile[]) => setFiles(Array.isArray(items) ? items : []))
      .catch(() => setFiles([]));
  }, [sessionId]);
  useEffect(load, [load, revision]);
  useEffect(() => {
    window.addEventListener(FILES_CHANGED, load);
    return () => window.removeEventListener(FILES_CHANGED, load);
  }, [load]);
  useCopilotReadable({
    description: "Files the user uploaded in this conversation",
    value: files.length
      ? files
          .map((f) =>
            f.table_name
              ? `${f.name} (workspace table ${f.table_name}; query it with query_workspace)`
              : `${f.name} (${f.mime_type}; stored in Artifacts, content not readable by you)`,
          )
          .join("; ")
      : "None.",
  });
  return null;
}

/** Tells the agent which connection/table the user picked in Explorer (AG-UI context reaches the ADK agent). */
function ScopeContext({ scope, connection }: { scope: TableScope | null; connection: ActiveConnection | null }) {
  let value: string;
  if (scope) {
    value =
      (scope.others?.length
        ? `Scoped to tables ${[scope.path, ...scope.others].map((p) => p.join(".")).join(", ")}`
        : `Scoped to table ${scope.path.join(".")}`) +
      ` in connection "${scope.connectionName}" (connection_id ${scope.connectionId}). ` +
      "Answer about these tables unless the user clearly asks about something else; no need to list other tables first.";
  } else if (connection?.chosen) {
    value =
      `Active connection "${connection.name}" (${connection.kind}, connection_id ${connection.id}), chosen by the user; ` +
      "no table is selected. Find tables only in this connection; do not call list_connections or use other " +
      "connections unless the user asks.";
  } else if (connection) {
    value =
      `No table is selected. Explorer shows connection "${connection.name}" (${connection.kind}, connection_id ` +
      `${connection.id}) only by default; the user has not chosen a connection.`;
  } else {
    value = "No connection or table is selected in Explorer.";
  }
  useCopilotReadable({ description: "The connection and table the user selected in the Explorer tab, if any", value });
  return null;
}

function ChatRenderers() {
  useAgentStepRenderers();
  return null;
}

export default function AppShell({
  theme,
  role,
  userId,
  site,
  demo,
  demoOrg = false,
  personal = false,
}: {
  theme: Theme;
  role?: string;
  userId?: string;
  site: SiteInfo;
  /** Set for live-demo visitors: demo prompts and the "keep using EasyQuery" banner. */
  demo?: DemoVisitor;
  /** The organization is the live-demo organization (its admins see demo visitors in the Organization tab). */
  demoOrg?: boolean;
  /** Personal accounts may import chats from an earlier live demo. */
  personal?: boolean;
}) {
  // The Organization tab (people, invites) is for tenant admins only; the API enforces it too.
  const { envs: collibraEnvs } = useCollibraEnvironments();
  const collibraBridge = useCollibraBridge();
  const [collibraScope, setCollibraScope] = useState<CollibraScope | null>(null);
  // Collibra appears only for organizations whose admin set it up (Settings → Collibra).
  const tabs = TABS.filter(
    (t) => (t.id !== "organization" || role === "admin") && (t.id !== "collibra" || (collibraEnvs?.length ?? 0) > 0),
  );
  const [panelOpen, setPanelOpen] = useState(true);
  const [tab, setTab] = useState<TabId>("about");
  const [resultsUnseen, setResultsUnseen] = useState(false);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const { items: conversations, error: conversationsError, remove: forgetConversation } = useConversations(revision);
  const [scope, setScope] = useState<TableScope | null>(null);
  const [connection, setConnection] = useState<ActiveConnection | null>(null);
  const [workbenchOpen, setWorkbenchOpen] = useState(false);
  const [workbenchFocus, setWorkbenchFocus] = useState(false);
  const [sqlRequest, setSqlRequest] = useState<SqlRequest | null>(null);
  const openSqlInWorkbench = useCallback((sql: string) => setSqlRequest({ sql, nonce: Date.now() }), []);
  const [shareOpen, setShareOpen] = useState(false);
  const [workbenchHost, setWorkbenchHost] = useState<HTMLDivElement | null>(null);
  const tabRefs = useRef<Partial<Record<TabId, HTMLButtonElement | null>>>({});
  const deepLink = useRef<string | null>(null);

  useEffect(() => {
    // Links from standalone pages (e.g. the Insight Wall): /?new=1 starts a conversation, /?tab=<id> opens a tab.
    // Read once per mount (the ref survives React's dev double-run after the URL is cleaned below).
    deepLink.current ??= window.location.search;
    const params = new URLSearchParams(deepLink.current);
    const wanted = TABS.find((t) => t.id === params.get("tab") && (t.id !== "organization" || role === "admin"))?.id;
    const fresh = params.get("new") === "1";
    if (wanted) setTab(wanted);
    if (!wanted && localStorage.getItem(PANEL_OPEN_KEY) === "false") setPanelOpen(false);
    // Reload reopens the conversation the user was last in.
    setThreadId((!fresh && localStorage.getItem(THREAD_KEY)) || crypto.randomUUID());
    if (params.has("tab") || params.has("new")) {
      params.delete("tab");
      params.delete("new");
      const rest = params.toString();
      window.history.replaceState(null, "", `${window.location.pathname}${rest ? `?${rest}` : ""}`);
    }
  }, []);

  useEffect(() => {
    if (threadId) localStorage.setItem(THREAD_KEY, threadId);
  }, [threadId]);

  const bump = useCallback(() => setRevision((r) => r + 1), []);
  // New threads use their thread id as the ADK session id; older ones keep a generated session id.
  const sessionId = conversations?.find((c) => c.threadId === threadId)?.sessionId ?? threadId ?? "";

  const showPanel = useCallback((open: boolean) => {
    setPanelOpen(open);
    setWorkbenchFocus(false);
    localStorage.setItem(PANEL_OPEN_KEY, String(open));
  }, []);

  // Focus never touches the saved panel preference: it only hides the panel while the workbench is open.
  const openWorkbench = useCallback((open: boolean) => {
    setWorkbenchOpen(open);
    setWorkbenchFocus(open && localStorage.getItem(WORKBENCH_FOCUS_KEY) === "true");
  }, []);
  const changeWorkbenchFocus = useCallback((on: boolean) => {
    setWorkbenchFocus(on);
    localStorage.setItem(WORKBENCH_FOCUS_KEY, String(on));
  }, []);

  // "Ask EasyQuery about this result": leave the workbench and pre-fill (never send) the chat input.
  const askAbout = useCallback((text: string) => {
    openWorkbench(false);
    window.setTimeout(() => {
      const input = document.querySelector<HTMLTextAreaElement>(".odn-chat textarea");
      if (!input) return;
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set?.call(input, text);
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.focus();
      input.setSelectionRange(text.length, text.length);
    }, 0);
  }, [openWorkbench]);
  const sidePanelShown = panelOpen && !workbenchFocus;

  const selectTab = useCallback((id: TabId) => {
    setTab(id);
    if (id === "results") setResultsUnseen(false);
  }, []);

  const showResults = useCallback(() => {
    showPanel(true);
    selectTab("results");
  }, [showPanel, selectTab]);

  // New results open the Results tab, except while the user is editing Settings (then just flag it).
  const onNewResult = useCallback(() => {
    if (tab === "settings") setResultsUnseen(true);
    else showResults();
  }, [tab, showResults]);

  const startNewConversation = useCallback(() => {
    setThreadId(crypto.randomUUID());
    setResultsUnseen(false);
  }, []);

  const openConversation = useCallback((id: string) => {
    setThreadId(id);
    setResultsUnseen(false);
  }, []);

  const onConversationDeleted = useCallback(
    (id: string) => {
      forgetConversation(id);
      if (id === threadId) startNewConversation();
      bump();
    },
    [threadId, startNewConversation, bump, forgetConversation],
  );

  function onTabKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    const index = tabs.findIndex((t) => t.id === tab);
    const next =
      e.key === "ArrowRight" ? (index + 1) % tabs.length
      : e.key === "ArrowLeft" ? (index - 1 + tabs.length) % tabs.length
      : e.key === "Home" ? 0
      : e.key === "End" ? tabs.length - 1
      : null;
    if (next === null) return;
    e.preventDefault();
    selectTab(tabs[next].id);
    tabRefs.current[tabs[next].id]?.focus();
  }

  const panel = (
    <section className={`odn-panel${tab === "about" ? " odn-decor" : ""}`} aria-label="Side panel">
      <div className="odn-tabs">
        <div role="tablist" aria-label="Side panel" className="odn-tablist" onKeyDown={onTabKeyDown}>
          {tabs.map((t) => (
            <button
              key={t.id}
              ref={(el) => {
                tabRefs.current[t.id] = el;
              }}
              type="button"
              role="tab"
              id={`tab-${t.id}`}
              aria-selected={tab === t.id}
              aria-controls={`tabpanel-${t.id}`}
              tabIndex={tab === t.id ? 0 : -1}
              onClick={() => selectTab(t.id)}
              className="odn-tab"
            >
              {t.label}
              {t.id === "results" && resultsUnseen && (
                <>
                  <span aria-hidden className="odn-tab-dot" />
                  <span className="sr-only"> (new)</span>
                </>
              )}
            </button>
          ))}
        </div>
        <button type="button" className="odn-panel-hide" onClick={() => showPanel(false)} aria-label="Hide side panel" title="Hide side panel">
          Hide ✕
        </button>
      </div>

      {/* All tab panels stay mounted so switching keeps explorer, results and settings state. */}
      <div role="tabpanel" id="tabpanel-about" aria-labelledby="tab-about" hidden={tab !== "about"} className="odn-tabpanel odn-tabpanel--about">
        <AboutSlides slides={theme.about} label={`About ${theme.product}`} />
      </div>
      <div role="tabpanel" id="tabpanel-chats" aria-labelledby="tab-chats" hidden={tab !== "chats"} className="odn-tabpanel">
        {threadId && (
          <ChatsPanel
            threadId={threadId}
            items={conversations}
            error={conversationsError}
            onOpen={openConversation}
            onNew={startNewConversation}
            onDeleted={onConversationDeleted}
          />
        )}
      </div>
      <div role="tabpanel" id="tabpanel-explorer" aria-labelledby="tab-explorer" hidden={tab !== "explorer"} className="odn-tabpanel">
        <Explorer
          sqlRequest={sqlRequest}
          sessionId={sessionId}
          scope={scope}
          onScopeChange={setScope}
          onConnectionChange={setConnection}
          workbenchHost={workbenchHost}
          workbenchOpen={workbenchOpen}
          onWorkbenchOpenChange={openWorkbench}
          focus={workbenchFocus}
          onFocusChange={changeWorkbenchFocus}
          onAskAbout={askAbout}
        />
      </div>
      {(collibraEnvs?.length ?? 0) > 0 && (
        <div role="tabpanel" id="tabpanel-collibra" aria-labelledby="tab-collibra" hidden={tab !== "collibra"} className="odn-tabpanel">
          <CollibraPanel envs={collibraEnvs ?? []} scope={collibraScope} onScopeChange={setCollibraScope} />
        </div>
      )}
      <div role="tabpanel" id="tabpanel-results" aria-labelledby="tab-results" hidden={tab !== "results"} className="odn-tabpanel">
        <div className="odn-sheet">
          <ResultsPanel />
        </div>
      </div>
      <div role="tabpanel" id="tabpanel-artifacts" aria-labelledby="tab-artifacts" hidden={tab !== "artifacts"} className="odn-tabpanel">
        {sessionId && (
          <ArtifactsPanel
            sessionId={sessionId}
            conversations={conversations}
            revision={revision}
            onOpenConversation={openConversation}
          />
        )}
      </div>
      <div role="tabpanel" id="tabpanel-settings" aria-labelledby="tab-settings" hidden={tab !== "settings"} className="odn-tabpanel">
        <div className="odn-sheet">
          <Settings role={role} collibraEnvs={collibraEnvs} />
        </div>
      </div>
      {role === "admin" && (
        <div role="tabpanel" id="tabpanel-organization" aria-labelledby="tab-organization" hidden={tab !== "organization"} className="odn-tabpanel">
          <div className="odn-sheet">{tab === "organization" && <Organization currentUserId={userId} demo={demoOrg} />}</div>
        </div>
      )}
    </section>
  );

  const chat = (
    <section className="odn-chat" aria-label={`Chat with ${theme.product}`}>
      <div className={`odn-chat-toolbar${IS_DEV ? " odn-chat-toolbar--dev" : ""}`}>
        <div className="odn-chat-toolbar-actions">
          {!sidePanelShown && (
            <button type="button" className="odn-btn odn-btn--outline odn-btn--small" onClick={() => showPanel(true)}>
              Show side panel
            </button>
          )}
        </div>
        <div className="odn-chat-toolbar-actions">
          <AgentStepsToggle />
          <a className="odn-btn odn-btn--outline odn-btn--small" href="/insights"
             title="Insight Wall: answers your colleagues and the EasyQuery community shared">
            Insights
          </a>
          <button
            type="button"
            className="odn-btn odn-btn--outline odn-btn--small"
            onClick={() => setShareOpen(true)}
            disabled={!conversations?.some((c) => c.threadId === threadId)}
            title="Share this conversation with teammates"
          >
            Share
          </button>
          <button type="button" className="odn-btn odn-btn--small" onClick={startNewConversation}>
            New conversation
          </button>
        </div>
      </div>
      {demo ? (
        <DemoUpgradeBanner visitor={demo} />
      ) : (
        <TrialBanner
          onOpenSettings={() => {
            showPanel(true);
            selectTab("settings");
            window.dispatchEvent(new CustomEvent(SETTINGS_SECTION, { detail: "model" }));
          }}
        />
      )}
      {personal && <DemoImportBanner />}
      {collibraScope && (
        <div className="odn-scope-chip">
          Collibra: <code>{collibraScope.name}</code>
          <button type="button" aria-label="Clear Collibra asset" onClick={() => setCollibraScope(null)}>
            ✕
          </button>
        </div>
      )}
      {scope && (
        <div className="odn-scope-chip">
          Scoped to <code>{scope.path.join(".")}</code>
          {scope.others?.map((p) => (
            <code key={p.join(".")}>{p.join(".")}</code>
          ))}
          <button type="button" aria-label="Clear table scope" onClick={() => setScope(null)}>
            ✕
          </button>
        </div>
      )}
      <div className="odn-chat-body">
        <div className="odn-chat-messages" aria-live="polite" aria-atomic="false">
          <OpenSqlContext.Provider value={openSqlInWorkbench}>
          <CopilotChat
            markdownTagRenderers={{ pre: FoldableSql }}
            attachments={{
              enabled: Boolean(sessionId),
              accept: CHAT_ATTACH_ACCEPT,
              maxSize: UPLOAD_MAX_BYTES,
              onUpload: (file: File) => uploadChatAttachment(sessionId, file),
            }}
            icons={{
              uploadIcon: (
                <>
                  <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48" />
                  </svg>
                  <span className="sr-only">Attach a file (CSV/XLSX become tables you can ask about)</span>
                </>
              ),
            }}
            labels={{
              title: theme.product,
              initial: demo ? DEMO_PLACEHOLDER : "Ask anything about your data, e.g. “How many businesses per status in CA?”",
            }}
            {...(demo ? { suggestions: DEMO_SUGGESTIONS } : {})}
          />
          </OpenSqlContext.Provider>
        </div>
        <div ref={setWorkbenchHost} className="odn-workbench-host" />
      </div>
      {threadId && <ShareDialog threadId={threadId} open={shareOpen} onClose={() => setShareOpen(false)} />}
    </section>
  );

  if (!threadId) return null;

  return (
    <SiteContext.Provider value={site}>
    <CopilotKit runtimeUrl="/api/copilotkit" agent={AGENT_ID} threadId={threadId}>
      <ShowResultsContext.Provider value={showResults}>
      <ChatSessionContext.Provider value={sessionId}>
        <HistoryHydrator threadId={threadId} />
        <RunWatcher onNew={onNewResult} onRunEnd={bump} />
        <ScopeContext scope={scope} connection={connection} />
        <UploadsContext sessionId={sessionId} revision={revision} />
        <ChatRenderers />
        <CollibraBrowserTools envs={collibraEnvs} bridge={collibraBridge} scope={collibraScope} />
        <SplitPane
          className={`odn-main${sidePanelShown ? "" : " odn-main--collapsed"}`}
          storageKey="odn.split.shell"
          defaultFraction={0.38}
          minLeft={360}
          minRight={420}
          label="Resize side panel"
          left={panel}
          right={chat}
        />
      </ChatSessionContext.Provider>
      </ShowResultsContext.Provider>
    </CopilotKit>
    </SiteContext.Provider>
  );
}
