import type { NextRequest } from "next/server";

import { proxyToAgent } from "@/lib/agent-proxy";

export const dynamic = "force-dynamic";

export async function DELETE(req: NextRequest, { params }: { params: Promise<{ threadId: string }> }) {
  return proxyToAgent(req, `/conversations/${encodeURIComponent((await params).threadId)}`);
}
