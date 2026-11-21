import { config } from "./config";
import { SHARE_ID, type PublicShare } from "./share";

/** Server side: the "anyone with the link" summary of a share, or null (not public, revoked, expired or unknown). */
export async function fetchPublicShare(id: string): Promise<PublicShare | null> {
  if (!SHARE_ID.test(id)) return null;
  try {
    const res = await fetch(new URL(`/v1/public/shares/${id}`, config.apiUrl), { cache: "no-store" });
    return res.ok ? ((await res.json()) as PublicShare) : null;
  } catch {
    return null;
  }
}
