/** Browser-side helpers for the BFF proxy (/api/odn/* -> API /v1/*). */

export class ApiError extends Error {
  constructor(public status: number, public detail: unknown) {
    super(typeof detail === "string" ? detail : formatDetail(detail));
  }
}

function formatDetail(detail: unknown): string {
  // FastAPI validation errors: [{loc, msg}, ...]
  if (Array.isArray(detail)) {
    return detail
      .map((d) =>
        d && typeof d === "object" && "msg" in d
          ? String((d as { msg: unknown }).msg).replace(/^Value error, /, "")
          : JSON.stringify(d),
      )
      .join("; ");
  }
  return JSON.stringify(detail);
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api/odn/${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
  });
  if (res.status === 204) return undefined as T;
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, body?.detail ?? `Request failed (HTTP ${res.status})`);
  return body as T;
}

/** Fired after Settings changes so other panels (Explorer, trial banner) reload. */
export const CONNECTIONS_CHANGED = "odn:connections-changed";
export const LLM_CHANGED = "odn:llm-changed";

export function notify(event: string): void {
  window.dispatchEvent(new Event(event));
}

export type Connection = {
  id: string;
  name: string;
  kind: string;
  scope: "shared" | "personal";
  config: Record<string, string>;
  allowed_objects: string[];
};

export type Budget = {
  key_source: "platform" | "user";
  daily_token_limit: number;
  tokens_used_today: number;
  user_daily_token_limit: number | null;
  user_tokens_used_today: number | null;
  exceeded: boolean;
  exceeded_scope: "tenant" | "user" | null;
  resets_at: string;
};
