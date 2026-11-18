import { NextResponse, type NextRequest } from "next/server";

import { authApi, sameOrigin } from "@/lib/auth";

export const dynamic = "force-dynamic";

/** Password reset email (always the same answer, whether or not the account exists). */
export async function POST(req: NextRequest) {
  if (!sameOrigin(req)) return NextResponse.json({ detail: "bad origin" }, { status: 403 });
  const { organization, email } = (await req.json().catch(() => ({}))) as { organization?: string; email?: string };
  const res = await authApi("forgot", { organization, email });
  const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
  return NextResponse.json({ detail: body.detail ?? "If the account exists, a reset link is on its way." }, { status: res.status === 422 ? 422 : 202 });
}
