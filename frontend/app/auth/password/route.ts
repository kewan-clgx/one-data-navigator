import { NextResponse, type NextRequest } from "next/server";

import { idpSignIn, sameOrigin } from "@/lib/auth";
import { ORG_COOKIE, cookieOptions } from "@/lib/session";

export const dynamic = "force-dynamic";

/** Sign-in step 2 for password organizations: the API checks the password with Identity Platform. */
export async function POST(req: NextRequest) {
  if (!sameOrigin(req)) return NextResponse.json({ detail: "bad origin" }, { status: 403 });
  const { organization, email, password } = (await req.json().catch(() => ({}))) as Record<string, string | undefined>;
  const res = await idpSignIn("password", { organization, email, password }, email);
  if (res.ok && organization) res.cookies.set(ORG_COOKIE, organization.slice(0, 200), cookieOptions(365 * 24 * 3600));
  return res;
}
