import type { NextRequest } from "next/server";

import { proxyToAgent } from "@/lib/agent-proxy";

export const dynamic = "force-dynamic";

/** Moves the caller's demo chats into their account. */
export function POST(req: NextRequest) {
  return proxyToAgent(req, "/demo-chats/import", "{}");
}
