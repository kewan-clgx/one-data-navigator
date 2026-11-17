import { NextResponse, type NextRequest } from "next/server";

import { proxyToAgent } from "@/lib/agent-proxy";

export const dynamic = "force-dynamic";

/** A conversation's saved messages and state, to reopen it (ag_ui_adk's /agents/state, scoped to the caller). */
export async function POST(req: NextRequest) {
  const { threadId } = await req.json().catch(() => ({ threadId: undefined }));
  if (typeof threadId !== "string" || !threadId) {
    return NextResponse.json({ detail: "threadId is required" }, { status: 400 });
  }
  return proxyToAgent(req, "/agents/state", JSON.stringify({ threadId }));
}
