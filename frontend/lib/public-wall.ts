import "server-only";

import { config } from "./config";
import { PAGE_SIZE, type WallCard, type WallSort } from "./wall";

/** Server side: the public Insight Wall for readers who aren't signed in, or null when the API is unreachable. */
export async function fetchPublicWall(sort: WallSort, q: string, offset: number): Promise<WallCard[] | null> {
  const url = new URL("/v1/public/wall", config.apiUrl);
  url.searchParams.set("sort", sort);
  if (q) url.searchParams.set("q", q.slice(0, 100));
  if (offset) url.searchParams.set("offset", String(Math.min(Math.max(0, offset), 10_000)));
  try {
    // No cache: taking an item down (revoke, admin switch) must hide it at once.
    const res = await fetch(url, { cache: "no-store" });
    return res.ok ? ((await res.json()) as WallCard[]) : null;
  } catch {
    return null;
  }
}

export { PAGE_SIZE };
