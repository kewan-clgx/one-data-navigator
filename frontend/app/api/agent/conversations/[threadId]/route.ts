import type { NextRequest } from "next/server";

import { proxyToAgent } from "@/lib/agent-proxy";

export const dynamic = "force-dynamic";

export function DELETE(req: NextRequest, { params }: { params: { threadId: string } }) {
  return proxyToAgent(req, `/conversations/${encodeURIComponent(params.threadId)}`);
}
