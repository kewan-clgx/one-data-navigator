import * as client from "openid-client";
import { NextResponse } from "next/server";

import { callbackUrl, oidc, toBrowserUrl } from "@/lib/oidc";
import { AUTH_TX_COOKIE, cookieOptions, seal } from "@/lib/session";
import { config } from "@/lib/config";

export const dynamic = "force-dynamic";

const TX_MAX_AGE_S = 600;

export async function GET() {
  const codeVerifier = client.randomPKCECodeVerifier();
  const state = client.randomState();
  const nonce = client.randomNonce();
  const url = client.buildAuthorizationUrl(await oidc(), {
    redirect_uri: callbackUrl(),
    scope: config.scope,
    code_challenge: await client.calculatePKCECodeChallenge(codeVerifier),
    code_challenge_method: "S256",
    state,
    nonce,
    ...(config.prompt ? { prompt: config.prompt } : {}),
  });

  const res = NextResponse.redirect(toBrowserUrl(url));
  res.cookies.set(AUTH_TX_COOKIE, await seal({ state, codeVerifier, nonce }, TX_MAX_AGE_S), cookieOptions(TX_MAX_AGE_S));
  return res;
}
