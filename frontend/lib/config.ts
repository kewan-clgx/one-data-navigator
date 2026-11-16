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
  get sessionSecret() {
    return required("ODN_SESSION_SECRET");
  },
  get isLocal() {
    return process.env.ODN_ENV === "local";
  },
};
