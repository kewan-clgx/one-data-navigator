import * as client from "openid-client";
import { cookies } from "next/headers";
import { NextResponse, type NextRequest } from "next/server";

import { config } from "@/lib/config";
import { callbackUrl, oidc } from "@/lib/oidc";
import {
  AUTH_TX_COOKIE,
  MAX_SESSION_S,
  SESSION_COOKIE,
  cookieOptions,
  seal,
  sessionCookieAge,
  unseal,
  type AuthTransaction,
  type Session,
} from "@/lib/session";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const tx = await unseal<AuthTransaction>(cookies().get(AUTH_TX_COOKIE)?.value);
  if (!tx) return NextResponse.json({ error: "login transaction expired" }, { status: 400 });

  // Rebuild from the registered redirect URI so it matches exactly regardless of proxy/Host headers.
  const current = new URL(callbackUrl());
  current.search = req.nextUrl.search;

  let tokens: client.TokenEndpointResponse & client.TokenEndpointResponseHelpers;
  try {
    tokens = await client.authorizationCodeGrant(await oidc(), current, {
      pkceCodeVerifier: tx.codeVerifier,
      expectedState: tx.state,
      expectedNonce: tx.nonce,
      idTokenExpected: true,
    });
  } catch (err) {
    console.error("OIDC callback failed", err);
    return NextResponse.json({ error: "login failed" }, { status: 400 });
  }

  const claims = tokens.claims();
  const useIdToken = config.apiToken === "id_token";
  const bearer = useIdToken ? tokens.id_token : tokens.access_token;
  if (!bearer) return NextResponse.json({ error: "login failed" }, { status: 400 });
  const lifetime = useIdToken && claims ? claims.exp - Math.floor(Date.now() / 1000) : tokens.expiresIn();
  const maxAge = Math.min(lifetime ?? 3600, MAX_SESSION_S);
  if (maxAge <= 0) return NextResponse.json({ error: "login failed" }, { status: 400 });
  const now = Math.floor(Date.now() / 1000);
  const session: Session = {
    accessToken: bearer,
    expiresAt: now + maxAge,
    name: typeof claims?.name === "string" ? claims.name : undefined,
    email: typeof claims?.email === "string" ? claims.email : undefined,
    refreshToken: tokens.refresh_token,
    provider: "oidc",
    loginAt: now,
  };
  const age = sessionCookieAge(session);

  const res = NextResponse.redirect(new URL("/", config.publicUrl));
  res.cookies.set(SESSION_COOKIE, await seal(session, age), cookieOptions(age));
  res.cookies.delete(AUTH_TX_COOKIE);
  return res;
}
