import { NextResponse, type NextRequest } from "next/server";

import { config } from "@/lib/config";
import { SESSION_COOKIE } from "@/lib/session";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  if (req.headers.get("origin") !== new URL(config.publicUrl).origin) {
    return NextResponse.json({ error: "bad origin" }, { status: 403 });
  }
  // Back to an organization's sign-in link when signing out from it (/<slug> only; never an outside URL).
  const form = await req.formData().catch(() => null);
  const next = String(form?.get("next") ?? "");
  const target = /^\/[a-z0-9][a-z0-9-]{1,62}$/.test(next) ? next : "/";
  const res = NextResponse.redirect(new URL(target, config.publicUrl), 303);
  res.cookies.delete(SESSION_COOKIE);
  return res;
}
