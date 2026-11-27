import { NextResponse, type NextRequest } from "next/server";

import { config } from "@/lib/config";
import { freshSession } from "@/lib/auth";

export const dynamic = "force-dynamic";

type Params = { params: Promise<{ path: string[] }> };

/** BFF proxy: browser -> /api/odn/<path> -> API /v1/<path> with the session's bearer token. */
async function proxy(req: NextRequest, { params }: Params) {
  const { path } = await params;
  const session = await freshSession();
  if (!session) return NextResponse.json({ detail: "not signed in" }, { status: 401 });

  if (req.method !== "GET" && req.headers.get("origin") !== new URL(config.publicUrl).origin) {
    return NextResponse.json({ detail: "bad origin" }, { status: 403 });
  }
  // Sign-in endpoints are server-to-server only (the BFF's /auth/* routes call them directly).
  if (path[0] === "auth") return NextResponse.json({ detail: "not found" }, { status: 404 });
  if (path.some((s) => s === "." || s === ".." || /[\\/]/.test(s))) {
    return NextResponse.json({ detail: "bad path" }, { status: 400 });
  }

  const target = new URL(`/v1/${path.map(encodeURIComponent).join("/")}`, config.apiUrl);
  target.search = req.nextUrl.search;
  const upstream = await fetch(target, {
    method: req.method,
    headers: {
      authorization: `Bearer ${session.accessToken}`,
      "content-type": req.headers.get("content-type") ?? "application/json",
    },
    // Raw bytes so file uploads pass through unchanged (JSON bodies too).
    body: req.method === "GET" || req.method === "DELETE" ? undefined : await req.arrayBuffer(),
    cache: "no-store",
  });
  const headers: Record<string, string> = {
    "content-type": upstream.headers.get("content-type") ?? "application/json",
  };
  if (path.at(-1) === "metadata-index" || path.at(-1) === "column-profile") headers["cache-control"] = "private, no-store";
  for (const name of ["content-disposition", "x-content-type-options", "content-security-policy"]) {
    const value = upstream.headers.get(name);
    if (value) headers[name] = value;
  }
  return new NextResponse(upstream.body, { status: upstream.status, headers });
}

export { proxy as GET, proxy as POST, proxy as PUT, proxy as PATCH, proxy as DELETE };
