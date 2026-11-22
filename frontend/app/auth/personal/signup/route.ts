import { NextResponse, type NextRequest } from "next/server";

import { authApi, sameOrigin } from "@/lib/auth";

export const dynamic = "force-dynamic";

/** Personal account sign-up: the API emails a set-password link (same answer for new and existing emails). */
export async function POST(req: NextRequest) {
  if (!sameOrigin(req)) return NextResponse.json({ detail: "bad origin" }, { status: 403 });
  const { email, name } = (await req.json().catch(() => ({}))) as Record<string, string | undefined>;
  const res = await authApi("personal/signup", { email, name: name || null });
  const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
  return NextResponse.json({ detail: body.detail ?? "Sign-up is unavailable." }, { status: res.status });
}
