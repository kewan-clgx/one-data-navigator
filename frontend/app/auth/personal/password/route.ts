import { NextResponse, type NextRequest } from "next/server";

import { idpSignIn, sameOrigin } from "@/lib/auth";

export const dynamic = "force-dynamic";

/** Personal account email + password sign-in (403 until the email is verified). */
export async function POST(req: NextRequest) {
  if (!sameOrigin(req)) return NextResponse.json({ detail: "bad origin" }, { status: 403 });
  const { email, password } = (await req.json().catch(() => ({}))) as Record<string, string | undefined>;
  return idpSignIn("personal/password", { email, password }, email);
}
