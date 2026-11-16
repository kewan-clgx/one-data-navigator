import "server-only";

import * as client from "openid-client";

import { config } from "./config";

let configuration: Promise<client.Configuration> | null = null;

export function oidc(): Promise<client.Configuration> {
  configuration ??= client
    .discovery(
      new URL(config.issuer),
      config.clientId,
      config.clientSecret,
      undefined,
      config.isLocal ? { execute: [client.allowInsecureRequests] } : undefined,
    )
    .catch((err) => {
      configuration = null;
      throw err;
    });
  return configuration;
}

/** Rewrite a server-side IdP URL to its browser-facing origin (local Docker split-horizon only). */
export function toBrowserUrl(url: URL): URL {
  if (!config.publicIssuer) return url;
  const internal = new URL(config.issuer);
  const external = new URL(config.publicIssuer);
  if (url.origin !== internal.origin) return url;
  return new URL(url.pathname + url.search, external.origin);
}

export function callbackUrl(): string {
  return new URL("/auth/callback", config.publicUrl).toString();
}
