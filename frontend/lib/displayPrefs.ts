"use client";

import { useSyncExternalStore } from "react";

import { APPEARANCE_IDS, APPEARANCE_KEY, CODE_THEME_IDS, CODE_THEME_KEY } from "./htmlPrefs";

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

/** A persisted choice applied as a `data-*` attribute on <html> (read by themes/*.css). */
function htmlPref<T extends string>(key: string, attr: "appearance" | "codeTheme", ids: readonly T[], fallback: T) {
  const listeners = new Set<() => void>();
  const read = (): T => {
    try {
      const stored = localStorage.getItem(key);
      return ids.includes(stored as T) ? (stored as T) : fallback;
    } catch {
      return fallback;
    }
  };
  const apply = (id: T) => {
    if (id === fallback) delete document.documentElement.dataset[attr];
    else document.documentElement.dataset[attr] = id;
  };
  const set = (id: T) => {
    try {
      localStorage.setItem(key, id);
    } catch {
      // Storage unavailable; applies for this page only.
    }
    apply(id);
    listeners.forEach((listener) => listener());
  };
  const use = (): T =>
    useSyncExternalStore(
      (listener) => {
        listeners.add(listener);
        return () => listeners.delete(listener);
      },
      read,
      () => fallback,
    );
  return { set, use };
}

/** App appearance: "elegant" is the brand theme as designed; the others re-skin it (themes/appearance.css). */
export const APPEARANCES = [
  { id: "elegant", label: "Elegant", hint: "The brand look: dark ink panel, warm paper surfaces" },
  { id: "code-dark", label: "Code Dark", hint: "VS Code Dark Modern colours, easy on the eyes" },
  { id: "code-light", label: "Code Light", hint: "VS Code Light Modern colours, bright and neutral" },
  { id: "studio", label: "Studio", hint: "Clean neutral greys, one green accent, rounded cards (Nuxt UI style)" },
  { id: "studio-dark", label: "Studio Dark", hint: "Studio after dark: zinc greys with a green accent" },
] as const;
export type AppearanceId = (typeof APPEARANCES)[number]["id"];
const appearance = htmlPref<AppearanceId>(APPEARANCE_KEY, "appearance", APPEARANCE_IDS, "elegant");
export const setAppearance = appearance.set;
export const useAppearance = appearance.use;

/** SQL code style (workbench editor and chat SQL); "auto" follows the app appearance (themes/code.css). */
export const CODE_THEMES = [
  { id: "auto", label: "Match app" },
  { id: "elegant", label: "Elegant" },
  { id: "vscode-dark", label: "Code Dark" },
  { id: "vscode-light", label: "Code Light" },
] as const;
export type CodeThemeId = (typeof CODE_THEMES)[number]["id"];
const codeTheme = htmlPref<CodeThemeId>(CODE_THEME_KEY, "codeTheme", CODE_THEME_IDS, "auto");
export const setCodeTheme = codeTheme.set;
export const useCodeTheme = codeTheme.use;
