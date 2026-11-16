import { HttpAgent } from "@ag-ui/client";
import {
  CopilotRuntime,
  ExperimentalEmptyAdapter,
  copilotRuntimeNextJSAppRouterEndpoint,
} from "@copilotkit/runtime";
import { NextResponse, type NextRequest } from "next/server";

import { config } from "@/lib/config";
import { getSession } from "@/lib/session";

export const dynamic = "force-dynamic";

const AGENT_ID = "odn";

export async function POST(req: NextRequest) {
  const session = await getSession();
  if (!session) return NextResponse.json({ detail: "not signed in" }, { status: 401 });
  if (req.headers.get("origin") !== new URL(config.publicUrl).origin) {
    return NextResponse.json({ detail: "bad origin" }, { status: 403 });
  }

  // Built per request so the agent call carries this user's token (the agent forwards it to the API).
  const runtime = new CopilotRuntime({
    agents: {
      [AGENT_ID]: new HttpAgent({
        url: `${config.agentUrl}/agent`,
        headers: { Authorization: `Bearer ${session.accessToken}` },
      }),
    },
  });
  const { handleRequest } = copilotRuntimeNextJSAppRouterEndpoint({
    runtime,
    serviceAdapter: new ExperimentalEmptyAdapter(),
    endpoint: "/api/copilotkit",
  });
  return handleRequest(req);
}
