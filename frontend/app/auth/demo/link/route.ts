import { NextResponse, type NextRequest } from "next/server";

import { sameOrigin } from "@/lib/auth";
import { demoSignIn } from "@/lib/demo";

export const dynamic = "force-dynamic";

/** The emailed "reopen your demo chats" link (posted by /try/continue). */
export async function POST(req: NextRequest) {
  if (!sameOrigin(req)) return NextResponse.json({ detail: "bad origin" }, { status: 403 });
  const { token } = (await req.json().catch(() => ({}))) as { token?: string };
  return demoSignIn("demo/link", { token }, {});
}
