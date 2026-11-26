"use client";

import { useSyncExternalStore } from "react";

const STORAGE_KEY = "odn.showAgentSteps";
const listeners = new Set<() => void>();

function read(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) !== "false";
  } catch {
    return true;
  }
}

export function setShowAgentSteps(value: boolean) {
  try {
    localStorage.setItem(STORAGE_KEY, String(value));
  } catch {
    // Storage unavailable (private mode); the choice just won't persist.
  }
  listeners.forEach((listener) => listener());
}

/** Whether tool-call step cards (Preparing / Running / Done) are shown in the chat. */
export function useShowAgentSteps(): boolean {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    read,
    () => true,
  );
}

/** SQL code style (workbench editor and chat SQL); "easyquery" follows the brand theme's --code-* tokens. */
export const CODE_THEMES = [
  { id: "easyquery", label: "EasyQuery" },
  { id: "vscode-dark", label: "VS Code Dark" },
  { id: "vscode-light", label: "VS Code Light" },
] as const;
export type CodeThemeId = (typeof CODE_THEMES)[number]["id"];

const CODE_THEME_KEY = "odn.sql.theme";
const codeThemeListeners = new Set<() => void>();

function readCodeTheme(): CodeThemeId {
  try {
    const stored = localStorage.getItem(CODE_THEME_KEY);
    return CODE_THEMES.some((t) => t.id === stored) ? (stored as CodeThemeId) : "easyquery";
  } catch {
    return "easyquery";
  }
}

/** Applies the code style to the page (`data-code-theme` on <html>, read by themes/code.css). */
export function applyCodeTheme(id: CodeThemeId = readCodeTheme()) {
  if (id === "easyquery") delete document.documentElement.dataset.codeTheme;
  else document.documentElement.dataset.codeTheme = id;
}

export function setCodeTheme(id: CodeThemeId) {
  try {
    localStorage.setItem(CODE_THEME_KEY, id);
  } catch {
    // Storage unavailable; applies for this page only.
  }
  applyCodeTheme(id);
  codeThemeListeners.forEach((listener) => listener());
}

export function useCodeTheme(): CodeThemeId {
  return useSyncExternalStore(
    (listener) => {
      codeThemeListeners.add(listener);
      return () => codeThemeListeners.delete(listener);
    },
    readCodeTheme,
    () => "easyquery",
  );
}
