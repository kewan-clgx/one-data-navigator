import "server-only";

import { NextResponse, type NextRequest } from "next/server";

import { config } from "@/lib/config";
import { getSession } from "@/lib/session";

/** Forwards a browser request to the agent runtime with the session's bearer token. */
export async function proxyToAgent(req: NextRequest, path: string, body?: string): Promise<NextResponse> {
  const session = await getSession();
  if (!session) return NextResponse.json({ detail: "not signed in" }, { status: 401 });
  if (req.method !== "GET" && req.headers.get("origin") !== new URL(config.publicUrl).origin) {
    return NextResponse.json({ detail: "bad origin" }, { status: 403 });
  }
  const upstream = await fetch(new URL(path, config.agentUrl), {
    method: req.method,
    headers: { authorization: `Bearer ${session.accessToken}`, "content-type": "application/json" },
    body,
    cache: "no-store",
  });
  if (upstream.status === 204) return new NextResponse(null, { status: 204 });
  const data = await upstream.json().catch(() => ({ detail: "invalid agent response" }));
  return NextResponse.json(data, { status: upstream.status });
}
