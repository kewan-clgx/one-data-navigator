"use client";

import { useEffect, useState } from "react";

export type CheckStatus = "ok" | "free_preview" | "confirm" | "over_cap" | "rejected" | "error";
export type QueryCheck = { status: CheckStatus; message: string; bytes_estimated: number | null; cost_known: boolean };

const DEBOUNCE_MS = 800;
// Mid-edit SQL often doesn't parse yet; that's not worth a red message while the user is still typing.
const UNPARSED = /could not be parsed|parse/i;

/**
 * Live pre-run check (policy, free preview, dry-run cost, scan cap) for the SQL Run would send. Debounced, cached per
 * exact SQL and cancelled when the text changes again, so a typing pause costs at most one request. A failed check is
 * cached too (Run still applies every check), so a flaky network doesn't retry on every keystroke.
 */
export function useQueryCheck(connectionId: string | null, sql: string) {
  // Results per exact (connection, SQL): revisiting a text never re-checks it.
  const [results, setResults] = useState<Record<string, QueryCheck | "failed">>({});
  const text = sql.trim();
  const key = connectionId && text ? `${connectionId}\n${text}` : null;
  const known = key ? results[key] : undefined;

  useEffect(() => {
    if (!key || known !== undefined) return;
    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      try {
        const res = await fetch(`/api/odn/connections/${encodeURIComponent(connectionId!)}/query/check`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ sql: text }),
          signal: controller.signal,
        });
        if (!res.ok) throw new Error(String(res.status));
        const check = (await res.json()) as QueryCheck;
        setResults((r) => ({ ...r, [key]: check }));
      } catch (e) {
        if ((e as Error).name !== "AbortError") setResults((r) => ({ ...r, [key]: "failed" }));
      }
    }, DEBOUNCE_MS);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [key, known, connectionId, text]);

  if (!key) return { check: null, loading: false, failed: false };
  if (known === undefined) return { check: null, loading: true, failed: false };
  return known === "failed" ? { check: null, loading: false, failed: true } : { check: known, loading: false, failed: false };
}

const ICON: Record<CheckStatus, string> = {
  ok: "✓", free_preview: "✓", confirm: "!", over_cap: "!", rejected: "✕", error: "✕",
};

export function QueryCheckLine({
  check,
  loading,
  failed,
  fallback,
}: {
  check: QueryCheck | null;
  loading: boolean;
  failed: boolean;
  /** Shown when there is nothing to check (e.g. SQL over the conversation's results). */
  fallback?: string;
}) {
  let tone = "muted";
  let icon = "";
  let text = fallback ?? "";
  if (loading) {
    text = "Checking…";
  } else if (failed) {
    text = "Couldn't check this query; Run still applies every check.";
  } else if (check) {
    const pending = check.status === "rejected" && UNPARSED.test(check.message);
    tone = pending ? "muted" : check.status === "ok" || check.status === "free_preview" ? "ok" : check.status === "confirm" ? "warn" : "bad";
    icon = pending ? "" : ICON[check.status];
    text = pending ? "Waiting for complete SQL…" : check.message;
  }
  return (
    <div className={`sqled-check sqled-check--${tone}`} role="status" aria-live="polite">
      {icon && <span aria-hidden className="sqled-check-icon">{icon}</span>}
      <span>{text}</span>
    </div>
  );
}
