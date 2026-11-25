import "server-only";

import { NextResponse } from "next/server";

import { authApi } from "./auth";
import { SESSION_COOKIE, cookieOptions, seal, sessionCookieAge, type Session } from "./session";

/** The browser's demo device token (issued by the API at check-in): lets the same browser reopen its demo chats. */
export const DEMO_DEVICE_COOKIE = "odn_demo_device";
const DEVICE_MAX_AGE_S = 7 * 24 * 3600;

type DemoOut = { id_token?: string; expires_in?: number; device_token?: string; notice?: string | null; detail?: unknown };

/** Calls a demo endpoint of the API and, on success, starts the demo session (and remembers this browser). */
export async function demoSignIn(path: string, body: unknown, who: { name?: string; email?: string }): Promise<NextResponse> {
  const res = await authApi(path, body);
  const data = (await res.json().catch(() => ({}))) as DemoOut;
  if (!res.ok || !data.id_token) {
    const detail = typeof data.detail === "string" ? data.detail : "The live demo isn't available right now.";
    return NextResponse.json({ detail }, { status: res.ok ? 502 : res.status });
  }
  const now = Math.floor(Date.now() / 1000);
  const session: Session = {
    accessToken: data.id_token,
    expiresAt: now + (data.expires_in ?? 3600),
    name: who.name?.trim() || undefined,
    email: who.email?.trim().toLowerCase() || undefined,
    provider: "demo",
    loginAt: now,
  };
  const age = sessionCookieAge(session);
  const reply = NextResponse.json({ ok: true, notice: data.notice ?? null });
  reply.cookies.set(SESSION_COOKIE, await seal(session, age), cookieOptions(age));
  if (data.device_token) reply.cookies.set(DEMO_DEVICE_COOKIE, data.device_token, cookieOptions(DEVICE_MAX_AGE_S));
  return reply;
}
