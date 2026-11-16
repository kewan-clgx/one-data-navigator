import "server-only";

import { base64url, EncryptJWT, jwtDecrypt, type JWTPayload } from "jose";
import { cookies } from "next/headers";

import { config } from "./config";

export const SESSION_COOKIE = "odn_session";
export const AUTH_TX_COOKIE = "odn_auth_tx";

export type Session = {
  accessToken: string;
  expiresAt: number;
  name?: string;
  email?: string;
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

export async function getSession(): Promise<Session | null> {
  const session = await unseal<Session>(cookies().get(SESSION_COOKIE)?.value);
  if (!session || session.expiresAt <= Date.now() / 1000) return null;
  return session;
}
