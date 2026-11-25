import "server-only";

import { base64url, EncryptJWT, jwtDecrypt, type JWTPayload } from "jose";
import { cookies } from "next/headers";

import { config } from "./config";

export const SESSION_COOKIE = "odn_session";
export const AUTH_TX_COOKIE = "odn_auth_tx";
/** Last organization typed on the sign-in card (not sensitive; prefills the form). */
export const ORG_COOKIE = "odn_org";
/** Longest a sign-in lasts, however often its token is refreshed. */
export const MAX_SESSION_S = 8 * 3600;

export type Session = {
  /** Bearer token for the API (the access or ID token, per ODN_OIDC_API_TOKEN). */
  accessToken: string;
  expiresAt: number;
  name?: string;
  email?: string;
  /** Present when the IdP issued one; the BFF uses it to renew `accessToken` before it expires. */
  refreshToken?: string;
  /**
   * "idp" = Identity Platform password sign-in (refreshed via the API); "oidc" = the OIDC provider;
   * "demo" = live-demo check-in (API-signed token, no refresh: the visitor checks in again).
   */
  provider?: "oidc" | "idp" | "demo";
  /** Unix seconds of the interactive sign-in; caps the session at MAX_SESSION_S. */
  loginAt?: number;
};

export type AuthTransaction = {
  state: string;
  codeVerifier: string;
  nonce: string;
};

function key(): Uint8Array {
  const raw = base64url.decode(config.sessionSecret);
  if (raw.length !== 32) throw new Error("ODN_SESSION_SECRET must be 32 bytes, base64url-encoded");
  return raw;
}

export async function seal(payload: Record<string, unknown>, maxAgeS: number): Promise<string> {
  return new EncryptJWT(payload as JWTPayload)
    .setProtectedHeader({ alg: "dir", enc: "A256GCM" })
    .setIssuedAt()
    .setExpirationTime(`${maxAgeS}s`)
    .encrypt(key());
}

export async function unseal<T>(token: string | undefined): Promise<T | null> {
  if (!token) return null;
  try {
    const { payload } = await jwtDecrypt(token, key());
    return payload as T;
  } catch {
    return null;
  }
}

export function cookieOptions(maxAgeS: number) {
  return {
    httpOnly: true,
    secure: !config.isLocal,
    sameSite: "lax" as const,
    path: "/",
    maxAge: maxAgeS,
  };
}

/** The session if its token is still valid (no refresh; usable from server components). */
export async function getSession(): Promise<Session | null> {
  const session = await readSession();
  if (!session || session.expiresAt <= Date.now() / 1000) return null;
  return session;
}

/** The stored session even if its token has expired (it may still be refreshable). */
export async function readSession(): Promise<Session | null> {
  return unseal<Session>((await cookies()).get(SESSION_COOKIE)?.value);
}

/** Seconds the session cookie should live: to the sign-in cap if refreshable, else to token expiry. */
export function sessionCookieAge(session: Session): number {
  const now = Math.floor(Date.now() / 1000);
  if (session.refreshToken) return Math.max(0, (session.loginAt ?? now) + MAX_SESSION_S - now);
  return Math.max(0, Math.min(session.expiresAt - now, MAX_SESSION_S));
}

export function canRefresh(session: Session): boolean {
  const now = Math.floor(Date.now() / 1000);
  return Boolean(session.refreshToken) && (session.loginAt ?? now) + MAX_SESSION_S > now;
}
