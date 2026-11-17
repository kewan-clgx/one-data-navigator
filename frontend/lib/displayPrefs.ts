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
