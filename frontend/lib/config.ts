import "server-only";

function required(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is not set`);
  return value;
}

export const config = {
  get apiUrl() {
    return required("ODN_API_URL");
  },
  get agentUrl() {
    return required("ODN_AGENT_URL");
  },
  get publicUrl() {
    return required("ODN_PUBLIC_URL");
  },
  get issuer() {
    return required("ODN_OIDC_ISSUER");
  },
  /** Browser-facing issuer base when it differs from the server-side one (local Docker only). */
  get publicIssuer() {
    return process.env.ODN_OIDC_PUBLIC_ISSUER || "";
  },
  get clientId() {
    return required("ODN_OIDC_CLIENT_ID");
  },
  get clientSecret() {
    return required("ODN_OIDC_CLIENT_SECRET");
  },
  get scope() {
    return process.env.ODN_OIDC_SCOPE || "openid profile email";
  },
  /**
   * Token forwarded to the API as the bearer: "access_token" (default; a JWT from most IdPs) or
   * "id_token" (Google, whose access tokens are opaque and cannot be verified by the API).
   */
  get apiToken(): "access_token" | "id_token" {
    return process.env.ODN_OIDC_API_TOKEN === "id_token" ? "id_token" : "access_token";
  },
  /** Optional OIDC "prompt", e.g. "select_account" so Google users can pick personal vs. corporate. */
  get prompt() {
    return process.env.ODN_OIDC_PROMPT || "";
  },
  /** Optional Google "access_type" ("offline" asks Google for a refresh token so sessions can renew). */
  get accessType() {
    return process.env.ODN_OIDC_ACCESS_TYPE || "";
  },
  get sessionSecret() {
    return required("ODN_SESSION_SECRET");
  },
  get isLocal() {
    return process.env.ODN_ENV === "local";
  },
};
