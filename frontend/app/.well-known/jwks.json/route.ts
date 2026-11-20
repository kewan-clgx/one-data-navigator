import { NextResponse } from "next/server";

import { config } from "@/lib/config";

/** QueryMuse's public signing keys (from the API), which Collibra uses to verify per-user tokens. */
export async function GET() {
  try {
    const res = await fetch(`${config.apiUrl}/.well-known/jwks.json`, { cache: "no-store" });
    if (!res.ok) return NextResponse.json({ keys: [] }, { status: 503 });
    return NextResponse.json(await res.json(), { headers: { "Cache-Control": "public, max-age=300" } });
  } catch {
    return NextResponse.json({ keys: [] }, { status: 503 });
  }
}
