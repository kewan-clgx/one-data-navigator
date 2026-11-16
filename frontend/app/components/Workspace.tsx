"use client";

import { useState } from "react";

import Chat from "./Chat";
import Explorer from "./Explorer";

const TABS = [
  { id: "chat", label: "Chat" },
  { id: "explorer", label: "Explorer" },
] as const;

export default function Workspace() {
  const [tab, setTab] = useState<(typeof TABS)[number]["id"]>("chat");
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
      {/* Both stay mounted so switching tabs keeps chat history and explorer state. */}
      <div hidden={tab !== "chat"}>
        <Chat />
      </div>
      <div hidden={tab !== "explorer"}>
        <Explorer />
      </div>
    </>
  );
}
