import { NextResponse, type NextRequest } from "next/server";

import { authApi, sameOrigin } from "@/lib/auth";

export const dynamic = "force-dynamic";

/** Personal account password reset email (same answer whether or not the account exists). */
export async function POST(req: NextRequest) {
  if (!sameOrigin(req)) return NextResponse.json({ detail: "bad origin" }, { status: 403 });
  const { email } = (await req.json().catch(() => ({}))) as { email?: string };
  const res = await authApi("personal/forgot", { email });
  const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
  const status = res.status === 404 || res.status === 422 ? res.status : 202;
  return NextResponse.json({ detail: body.detail ?? "If the account exists, a reset link is on its way." }, { status });
}
