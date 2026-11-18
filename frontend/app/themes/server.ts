import "server-only";

import { currentIdentity } from "@/lib/identity";

import { resolveTheme, THEMES, type Theme } from ".";

/** The deployment's theme, chosen with ODN_THEME (unknown or unset values fall back to the default). */
export function currentTheme(): Theme {
  return resolveTheme(process.env.ODN_THEME);
}

/** After sign-in: the tenant's theme if it is a known one, else the deployment theme. */
export async function tenantTheme(): Promise<Theme> {
  const identity = await currentIdentity();
  const id = identity.kind === "ok" ? identity.me.tenant.theme : null;
  return (id && THEMES[id]) || currentTheme();
}

/** Brand-bar company name: the tenant's name once signed in; none before (the tenant is not known yet). */
export async function brandCompany(): Promise<string | undefined> {
  const identity = await currentIdentity();
  return (identity.kind === "ok" && identity.me.tenant.name) || undefined;
}
