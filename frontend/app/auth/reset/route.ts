import { NextResponse, type NextRequest } from "next/server";

import { authApi, sameOrigin } from "@/lib/auth";
import { ORG_COOKIE, cookieOptions } from "@/lib/session";

export const dynamic = "force-dynamic";

/** Set-password page: checks the emailed link, or sets the password; then prefills the organization. */
export async function POST(req: NextRequest) {
  if (!sameOrigin(req)) return NextResponse.json({ detail: "bad origin" }, { status: 403 });
  const { oobCode, tenantId, newPassword } = (await req.json().catch(() => ({}))) as Record<string, string | undefined>;
  const res = await authApi("reset", { oob_code: oobCode, tenant_id: tenantId, new_password: newPassword ?? null });
  const body = (await res.json().catch(() => ({}))) as { email?: string; organization?: string | null; detail?: unknown };
  if (!res.ok) {
    const detail = typeof body.detail === "string" ? body.detail : "This link can't be used. Ask for a new one.";
    return NextResponse.json({ detail }, { status: res.status });
  }
  const reply = NextResponse.json({ email: body.email, organization: body.organization ?? null });
  if (newPassword && body.organization) reply.cookies.set(ORG_COOKIE, body.organization, cookieOptions(365 * 24 * 3600));
  return reply;
}
