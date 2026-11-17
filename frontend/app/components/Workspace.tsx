"use client";

import { useCallback, useEffect, useState } from "react";

import { api, LLM_CHANGED, type Budget } from "@/lib/client-api";

import Chat from "./Chat";
import Explorer from "./Explorer";
import Settings from "./Settings";

const TABS = [
  { id: "chat", label: "Chat" },
  { id: "explorer", label: "Explorer" },
  { id: "settings", label: "Settings" },
] as const;

type TabId = (typeof TABS)[number]["id"];

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

export default function Workspace() {
  const [tab, setTab] = useState<TabId>("chat");
  return (
    <>
      <nav className="tabs" role="tablist" aria-label="Workspace">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={tab === t.id}
            className={tab === t.id ? "active" : ""}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </nav>
      {/* All stay mounted so switching tabs keeps chat history, explorer and settings state. */}
      <div hidden={tab !== "chat"}>
        <TrialBanner onOpenSettings={() => setTab("settings")} />
        <Chat />
      </div>
      <div hidden={tab !== "explorer"}>
        <Explorer />
      </div>
      <div hidden={tab !== "settings"}>
        <Settings />
      </div>
    </>
  );
}
