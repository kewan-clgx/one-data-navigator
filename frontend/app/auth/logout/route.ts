import { NextResponse, type NextRequest } from "next/server";

import { config } from "@/lib/config";
import { SESSION_COOKIE } from "@/lib/session";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  if (req.headers.get("origin") !== new URL(config.publicUrl).origin) {
    return NextResponse.json({ error: "bad origin" }, { status: 403 });
  }
  const res = NextResponse.redirect(new URL("/", config.publicUrl), 303);
  res.cookies.delete(SESSION_COOKIE);
  return res;
}
