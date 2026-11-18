import { NextResponse } from "next/server";

import { freshSession } from "@/lib/auth";
import { config } from "@/lib/config";
import { SESSION_COOKIE } from "@/lib/session";

export const dynamic = "force-dynamic";

/** Page loads with an expired but renewable token land here (server components cannot set cookies). */
export async function GET() {
  const session = await freshSession();
  const res = NextResponse.redirect(new URL("/", config.publicUrl), 303);
  if (!session) res.cookies.delete(SESSION_COOKIE);
  return res;
}
