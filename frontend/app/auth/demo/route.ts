import { cookies } from "next/headers";
import { NextResponse, type NextRequest } from "next/server";

import { sameOrigin } from "@/lib/auth";
import { DEMO_DEVICE_COOKIE, demoSignIn } from "@/lib/demo";

export const dynamic = "force-dynamic";

/** Live-demo check-in: name + email (not verified) -> a demo session in the demo organization. */
export async function POST(req: NextRequest) {
  if (!sameOrigin(req)) return NextResponse.json({ detail: "bad origin" }, { status: 403 });
  const { name, email } = (await req.json().catch(() => ({}))) as { name?: string; email?: string };
  // The proxy in front of us (Caddy) appends the visitor's address; the API rate-limits check-ins per address.
  const clientIp = req.headers.get("x-forwarded-for")?.split(",")[0]?.trim().slice(0, 64) || null;
  const device = (await cookies()).get(DEMO_DEVICE_COOKIE)?.value || null;
  return demoSignIn("demo", { name, email, client_ip: clientIp, device_token: device }, { name, email });
}
