import * as client from "openid-client";
import { cookies } from "next/headers";
import { NextResponse, type NextRequest } from "next/server";

import { config } from "@/lib/config";
import { callbackUrl, oidc } from "@/lib/oidc";
import { AUTH_TX_COOKIE, SESSION_COOKIE, cookieOptions, seal, unseal, type AuthTransaction } from "@/lib/session";

export const dynamic = "force-dynamic";

const MAX_SESSION_S = 8 * 3600;

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
  const maxAge = Math.min(tokens.expiresIn() ?? 3600, MAX_SESSION_S);
  const session = {
    accessToken: tokens.access_token,
    expiresAt: Math.floor(Date.now() / 1000) + maxAge,
    name: typeof claims?.name === "string" ? claims.name : undefined,
    email: typeof claims?.email === "string" ? claims.email : undefined,
  };

  const res = NextResponse.redirect(new URL("/", config.publicUrl));
  res.cookies.set(SESSION_COOKIE, await seal(session, maxAge), cookieOptions(maxAge));
  res.cookies.delete(AUTH_TX_COOKIE);
  return res;
}
