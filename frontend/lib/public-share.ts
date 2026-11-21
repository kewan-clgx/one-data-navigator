import { config } from "./config";
import { SHARE_ID, type PublicShare } from "./share";

/** Server side: the "anyone with the link" summary of a share, or null (not public, revoked, expired or unknown). */
export async function fetchPublicShare(id: string): Promise<PublicShare | null> {
  const r = await loadPublicShare(id);
  return r === "unavailable" ? null : r;
}

/** Like fetchPublicShare, but tells "not public / unknown" (null) apart from "API unreachable". */
export async function loadPublicShare(id: string): Promise<PublicShare | null | "unavailable"> {
  if (!SHARE_ID.test(id)) return null;
  try {
    const res = await fetch(new URL(`/v1/public/shares/${id}`, config.apiUrl), { cache: "no-store" });
    if (res.ok) return (await res.json()) as PublicShare;
    return res.status === 404 ? null : "unavailable";
  } catch {
    return "unavailable";
  }
}
