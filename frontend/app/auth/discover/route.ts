import { NextResponse, type NextRequest } from "next/server";

import { authApi, sameOrigin } from "@/lib/auth";
import { ORG_COOKIE, cookieOptions } from "@/lib/session";

export const dynamic = "force-dynamic";

const ORG_COOKIE_AGE_S = 365 * 24 * 3600;

/** Sign-in step 1: organization + work email -> how this organization signs in. */
export async function POST(req: NextRequest) {
  if (!sameOrigin(req)) return NextResponse.json({ detail: "bad origin" }, { status: 403 });
  const { organization, email } = (await req.json().catch(() => ({}))) as { organization?: string; email?: string };
  const res = await authApi("discover", { organization, email });
  const body = (await res.json().catch(() => ({}))) as { method?: string; organization?: string; hd?: string | null; detail?: unknown };
  if (!res.ok) return NextResponse.json({ detail: body.detail ?? "Sign-in is unavailable." }, { status: res.status });

  let out: { method: string; organization?: string; redirect?: string } = { method: body.method ?? "", organization: body.organization };
  if (body.method === "google" || body.method === "oidc") {
    const q = new URLSearchParams({ login_hint: String(email ?? "") });
    if (body.hd) q.set("hd", body.hd);
    out = { ...out, redirect: `/auth/login?${q}` };
  }
  const reply = NextResponse.json(out);
  reply.cookies.set(ORG_COOKIE, String(organization ?? "").slice(0, 200), cookieOptions(ORG_COOKIE_AGE_S));
  return reply;
}
