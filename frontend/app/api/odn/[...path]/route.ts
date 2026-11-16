import { NextResponse, type NextRequest } from "next/server";

import { config } from "@/lib/config";
import { getSession } from "@/lib/session";

export const dynamic = "force-dynamic";

type Params = { params: { path: string[] } };

/** BFF proxy: browser -> /api/odn/<path> -> API /v1/<path> with the session's bearer token. */
async function proxy(req: NextRequest, { params }: Params) {
  const session = await getSession();
  if (!session) return NextResponse.json({ detail: "not signed in" }, { status: 401 });

  if (req.method !== "GET" && req.headers.get("origin") !== new URL(config.publicUrl).origin) {
    return NextResponse.json({ detail: "bad origin" }, { status: 403 });
  }
  if (params.path.some((s) => s === "." || s === ".." || /[\\/]/.test(s))) {
    return NextResponse.json({ detail: "bad path" }, { status: 400 });
  }

  const target = new URL(`/v1/${params.path.map(encodeURIComponent).join("/")}`, config.apiUrl);
  target.search = req.nextUrl.search;
  const upstream = await fetch(target, {
    method: req.method,
    headers: {
      authorization: `Bearer ${session.accessToken}`,
      "content-type": req.headers.get("content-type") ?? "application/json",
    },
    body: req.method === "GET" || req.method === "DELETE" ? undefined : await req.text(),
    cache: "no-store",
  });
  const headers: Record<string, string> = {
    "content-type": upstream.headers.get("content-type") ?? "application/json",
  };
  const disposition = upstream.headers.get("content-disposition");
  if (disposition) headers["content-disposition"] = disposition;
  return new NextResponse(upstream.body, { status: upstream.status, headers });
}

export { proxy as GET, proxy as POST, proxy as PUT, proxy as DELETE };
