import "server-only";

import { config } from "./config";

/** An organization's sign-in link (/<slug>), as the API describes it without sign-in. */
export type Entry = {
  slug: string;
  organization: string;
  theme: string;
  method: "google" | "oidc" | "password";
  hd: string | null;
  personal: boolean;
};

export const SLUG = /^[a-z0-9][a-z0-9-]{1,62}$/;

export async function fetchEntry(slug: string): Promise<Entry | null> {
  if (!SLUG.test(slug)) return null;
  try {
    const res = await fetch(new URL(`/v1/auth/entry/${slug}`, config.apiUrl), { cache: "no-store" });
    return res.ok ? ((await res.json()) as Entry) : null;
  } catch {
    return null;
  }
}
