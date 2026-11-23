import "server-only";

/** Public-facing addresses that depend on the deployment's domain (docs/config/plan_to_easyquery.md). */

/** The app's origin, e.g. https://easyquery.ai (sign-in, reports, emails). */
export function appUrl(): string {
  return (process.env.ODN_PUBLIC_URL || "").replace(/\/$/, "");
}

/** The app's host name for text, e.g. "easyquery.ai". */
export function appHost(): string {
  try {
    return new URL(appUrl()).host;
  } catch {
    return "localhost";
  }
}

export function supportEmail(): string {
  return process.env.ODN_SUPPORT_EMAIL || "support@easyquery.ai";
}

/** Where the public Insight Wall lives: its own host when configured, else the app's /insights page. */
export function wallAddress(): string {
  const host = process.env.ODN_INSIGHTS_HOSTNAME;
  return host && !host.endsWith("localhost") ? host : `${appHost()}/insights`;
}
