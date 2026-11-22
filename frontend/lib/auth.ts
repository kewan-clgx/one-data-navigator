import "server-only";

import * as client from "openid-client";
import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import { config } from "./config";
import { oidc } from "./oidc";
import { canRefresh, cookieOptions, readSession, seal, sessionCookieAge, SESSION_COOKIE, type Session } from "./session";

/** Renew within this many seconds of expiry. */
const REFRESH_MARGIN_S = 120;

type ApiTokens = { id_token: string; refresh_token: string; expires_in: number };

/** POST to an unauthenticated /v1/auth endpoint of the API (server-to-server). */
export async function authApi(path: string, body: unknown): Promise<Response> {
  return fetch(new URL(`/v1/auth/${path}`, config.apiUrl), {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });
}

/** Relay an Identity Platform password sign-in: on success, start the session cookie. */
export async function idpSignIn(path: string, body: unknown, email: string | undefined): Promise<NextResponse> {
  const res = await authApi(path, body);
  const data = (await res.json().catch(() => ({}))) as Partial<ApiTokens> & { detail?: unknown };
  if (!res.ok || !data.id_token) {
    return NextResponse.json({ detail: data.detail ?? "Sign-in failed." }, { status: res.ok ? 502 : res.status });
  }
  const now = Math.floor(Date.now() / 1000);
  const session: Session = {
    accessToken: data.id_token,
    refreshToken: data.refresh_token,
    expiresAt: now + (data.expires_in ?? 3600),
    email: email?.trim(),
    provider: "idp",
    loginAt: now,
  };
  const age = sessionCookieAge(session);
  const reply = NextResponse.json({ ok: true });
  reply.cookies.set(SESSION_COOKIE, await seal(session, age), cookieOptions(age));
  return reply;
}

async function refreshTokens(session: Session): Promise<Session | null> {
  if (!session.refreshToken) return null;
  const now = Math.floor(Date.now() / 1000);
  if (session.provider === "idp") {
    const res = await authApi("refresh", { refresh_token: session.refreshToken });
    if (!res.ok) return null;
    const t = (await res.json()) as ApiTokens;
    return { ...session, accessToken: t.id_token, refreshToken: t.refresh_token, expiresAt: now + t.expires_in };
  }
  const tokens = await client.refreshTokenGrant(await oidc(), session.refreshToken);
  const bearer = config.apiToken === "id_token" ? tokens.id_token : tokens.access_token;
  if (!bearer) return null;
  const claims = tokens.claims();
  const lifetime = config.apiToken === "id_token" && claims ? claims.exp - now : tokens.expiresIn() ?? 3600;
  return {
    ...session,
    accessToken: bearer,
    refreshToken: tokens.refresh_token ?? session.refreshToken,
    expiresAt: now + lifetime,
  };
}

/**
 * The session for route handlers: renews the token shortly before it expires (and rewrites the cookie).
 * Returns null when signed out or no longer renewable. Only call where cookies may be set.
 */
export async function freshSession(): Promise<Session | null> {
  const session = await readSession();
  if (!session) return null;
  const now = Math.floor(Date.now() / 1000);
  if (session.expiresAt - now > REFRESH_MARGIN_S) return session;
  if (!canRefresh(session)) return session.expiresAt > now ? session : null;
  const renewed = await refreshTokens(session).catch(() => null);
  if (!renewed) return session.expiresAt > now ? session : null;
  const age = sessionCookieAge(renewed);
  cookies().set(SESSION_COOKIE, await seal(renewed, age), cookieOptions(age));
  return renewed;
}

/** Same-origin check for state-changing BFF routes. */
export function sameOrigin(req: Request): boolean {
  return req.headers.get("origin") === new URL(config.publicUrl).origin;
}
