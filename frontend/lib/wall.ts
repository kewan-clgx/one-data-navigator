/** Insight Wall types and helpers (docs/roadmap/insight_wall_dashboards_plan.md). */

import type { ChartData } from "@/app/components/Chart";

export type WallScope = "organization" | "public";
export type WallSort = "hot" | "new" | "top";
export type WallChoice = "none" | "organization" | "public";
export type WallCard = {
  id: string; kind: "conversation" | "answer"; title: string; question: string; answer: string;
  chart: { title: string; result: ChartData } | null; owner_name: string; organization: string | null;
  created_at: string; listed_at: string; expires_at: string; votes: number; follow_ups: number;
  featured: boolean; wall: "organization" | "public"; my_vote?: boolean; is_owner?: boolean;
};
export type WallSettings = { enabled: boolean; public_links: boolean; org_wall: boolean; public_wall: boolean; personal: boolean };
export type WallPage = { enabled: boolean; items: WallCard[]; settings: WallSettings };

export const WALL_SORTS: { id: WallSort; label: string }[] = [
  { id: "hot", label: "Hot" },
  { id: "new", label: "New" },
  { id: "top", label: "Top" },
];
export const PUBLIC_WALL_NAME = "EasyQuery public Insight Wall";
export const PAGE_SIZE = 30;

export function isSort(v: string | undefined): v is WallSort {
  return v === "hot" || v === "new" || v === "top";
}

export function timeAgo(iso: string, now = Date.now()): string {
  const s = Math.max(0, (now - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  const units: [number, string][] = [[60, "minute"], [3600, "hour"], [86400, "day"], [604800, "week"]];
  let [div, name] = units[0];
  for (const u of units) if (s >= u[0]) [div, name] = u;
  const n = Math.floor(s / div);
  return `${n} ${name}${n === 1 ? "" : "s"} ago`;
}

/** Where a link sits, as people read it. */
export function wallLabel(wall: WallChoice | string, organization = "your organization"): string {
  if (wall === "organization") return `${organization}'s Insight Wall`;
  if (wall === "public") return PUBLIC_WALL_NAME;
  return "not on a wall";
}
