import type { NextRequest } from "next/server";

import { proxyToAgent } from "@/lib/agent-proxy";

export const dynamic = "force-dynamic";

/** Demo chats a personal account (verified email) can import. */
export function GET(req: NextRequest) {
  return proxyToAgent(req, "/demo-chats");
}
