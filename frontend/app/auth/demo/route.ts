import { NextResponse, type NextRequest } from "next/server";

import { authApi, sameOrigin } from "@/lib/auth";
import { SESSION_COOKIE, cookieOptions, seal, sessionCookieAge, type Session } from "@/lib/session";

export const dynamic = "force-dynamic";

/** Live-demo check-in: name + email (not verified) -> a demo session in the demo organization. */
export async function POST(req: NextRequest) {
  if (!sameOrigin(req)) return NextResponse.json({ detail: "bad origin" }, { status: 403 });
  const { name, email } = (await req.json().catch(() => ({}))) as { name?: string; email?: string };
  // The proxy in front of us (Caddy) appends the visitor's address; the API rate-limits check-ins per address.
  const clientIp = req.headers.get("x-forwarded-for")?.split(",")[0]?.trim().slice(0, 64) || null;
  const res = await authApi("demo", { name, email, client_ip: clientIp });
  const body = (await res.json().catch(() => ({}))) as { id_token?: string; expires_in?: number; detail?: unknown };
  if (!res.ok || !body.id_token) {
    const detail = typeof body.detail === "string" ? body.detail : "The live demo isn't available right now.";
    return NextResponse.json({ detail }, { status: res.ok ? 502 : res.status });
  }
  const now = Math.floor(Date.now() / 1000);
  const session: Session = {
    accessToken: body.id_token,
    expiresAt: now + (body.expires_in ?? 3600),
    name: name?.trim(),
    email: email?.trim().toLowerCase(),
    provider: "demo",
    loginAt: now,
  };
  const age = sessionCookieAge(session);
  const reply = NextResponse.json({ ok: true });
  reply.cookies.set(SESSION_COOKIE, await seal(session, age), cookieOptions(age));
  return reply;
}
