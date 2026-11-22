import { NextResponse, type NextRequest } from "next/server";

import { idpSignIn, sameOrigin } from "@/lib/auth";

export const dynamic = "force-dynamic";

/** Sign-in step 2 for password organizations: the API checks the password with Identity Platform. */
export async function POST(req: NextRequest) {
  if (!sameOrigin(req)) return NextResponse.json({ detail: "bad origin" }, { status: 403 });
  const { organization, email, password } = (await req.json().catch(() => ({}))) as Record<string, string | undefined>;
  return idpSignIn("password", { organization, email, password }, email);
}
