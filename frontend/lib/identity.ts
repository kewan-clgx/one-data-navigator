import "server-only";

import { cache } from "react";

import { config } from "./config";
import { getSession } from "./session";

export type TenantProfile = { slug: string; name: string | null; theme: string | null; auth_method: string | null };
export type Me = { role: string; user: { email: string | null; name: string | null }; tenant: TenantProfile };

/** The signed-in identity as the API resolves it: a profile, an access refusal, or no session. */
export type Identity =
  | { kind: "signed-out" }
  | { kind: "ok"; me: Me; displayName: string }
  | { kind: "refused"; detail: string; displayName: string };

/** One /v1/me call per request (layout, metadata and page share it). */
export const currentIdentity = cache(async (): Promise<Identity> => {
  const session = await getSession();
  if (!session) return { kind: "signed-out" };
  const displayName = session.name ?? session.email ?? "Signed in";
  try {
    const res = await fetch(new URL("/v1/me", config.apiUrl), {
      headers: { authorization: `Bearer ${session.accessToken}` },
      cache: "no-store",
    });
    if (res.ok) return { kind: "ok", me: (await res.json()) as Me, displayName };
    if (res.status === 401) return { kind: "signed-out" };
    const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
    return {
      kind: "refused",
      detail: typeof body.detail === "string" ? body.detail : "You do not have access to this organization.",
      displayName,
    };
  } catch {
    return { kind: "refused", detail: "The service is unavailable. Please try again shortly.", displayName };
  }
});
