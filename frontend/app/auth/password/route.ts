import { NextResponse, type NextRequest } from "next/server";

import { authApi, sameOrigin } from "@/lib/auth";
import { SESSION_COOKIE, cookieOptions, seal, sessionCookieAge, type Session } from "@/lib/session";

export const dynamic = "force-dynamic";

/** Sign-in step 2 for password organizations: the API checks the password with Identity Platform. */
export async function POST(req: NextRequest) {
  if (!sameOrigin(req)) return NextResponse.json({ detail: "bad origin" }, { status: 403 });
  const { organization, email, password } = (await req.json().catch(() => ({}))) as Record<string, string | undefined>;
  const res = await authApi("password", { organization, email, password });
  const body = (await res.json().catch(() => ({}))) as {
    id_token?: string;
    refresh_token?: string;
    expires_in?: number;
    detail?: unknown;
  };
  if (!res.ok || !body.id_token) {
    return NextResponse.json({ detail: body.detail ?? "Sign-in failed." }, { status: res.ok ? 502 : res.status });
  }
  const now = Math.floor(Date.now() / 1000);
  const session: Session = {
    accessToken: body.id_token,
    refreshToken: body.refresh_token,
    expiresAt: now + (body.expires_in ?? 3600),
    email: email?.trim(),
    provider: "idp",
    loginAt: now,
  };
  const age = sessionCookieAge(session);
  const reply = NextResponse.json({ ok: true });
  reply.cookies.set(SESSION_COOKIE, await seal(session, age), cookieOptions(age));
  return reply;
}
