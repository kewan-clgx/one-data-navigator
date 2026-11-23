import * as client from "openid-client";
import { NextResponse, type NextRequest } from "next/server";

import { callbackUrl, oidc, toBrowserUrl } from "@/lib/oidc";
import { AUTH_TX_COOKIE, ORG_COOKIE, cookieOptions, seal } from "@/lib/session";
import { config } from "@/lib/config";

export const dynamic = "force-dynamic";

const TX_MAX_AGE_S = 600;

export async function GET(req: NextRequest) {
  // Hints from the sign-in card: preselect the account and, for Google Workspace, the domain.
  const loginHint = req.nextUrl.searchParams.get("login_hint")?.slice(0, 320);
  const hd = req.nextUrl.searchParams.get("hd")?.slice(0, 253);
  // Personal "Continue with Google" asks Google to show the account chooser (personal vs. work accounts).
  const prompt = req.nextUrl.searchParams.get("prompt") === "select_account" ? "select_account" : config.prompt;
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
    ...(prompt ? { prompt } : {}),
    ...(config.accessType ? { access_type: config.accessType } : {}),
    ...(loginHint ? { login_hint: loginHint } : {}),
    ...(hd && /^[a-z0-9.-]+$/i.test(hd) ? { hd } : {}),
  });

  const res = NextResponse.redirect(toBrowserUrl(url));
  res.cookies.set(AUTH_TX_COOKIE, await seal({ state, codeVerifier, nonce }, TX_MAX_AGE_S), cookieOptions(TX_MAX_AGE_S));
  // From an organization's sign-in link (/<slug>): prefill it on the general sign-in page next time.
  const org = req.nextUrl.searchParams.get("org");
  if (org && /^[a-z0-9][a-z0-9-]{1,62}$/.test(org)) res.cookies.set(ORG_COOKIE, org, cookieOptions(365 * 24 * 3600));
  return res;
}
