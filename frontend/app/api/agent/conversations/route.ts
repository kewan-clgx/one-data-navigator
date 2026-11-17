import type { NextRequest } from "next/server";

import { proxyToAgent } from "@/lib/agent-proxy";

export const dynamic = "force-dynamic";

/** The signed-in user's conversations (Chats tab). */
export function GET(req: NextRequest) {
  return proxyToAgent(req, "/conversations");
}
