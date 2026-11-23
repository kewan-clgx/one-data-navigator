import type { Metadata } from "next";
import { redirect } from "next/navigation";

import BrandBar from "../components/BrandBar";
import InsightWall from "../components/InsightWall";
import { currentTheme, tenantTheme } from "../themes/server";
import { config } from "@/lib/config";
import { currentIdentity } from "@/lib/identity";
import { fetchPublicWall } from "@/lib/public-wall";
import { isSort, PUBLIC_WALL_NAME } from "@/lib/wall";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: `${PUBLIC_WALL_NAME} — answers people shared`,
  description: "Data questions and answers people shared on EasyQuery: upvote the useful ones and ask your own follow-up.",
  robots: { index: false, follow: false },
  openGraph: { title: PUBLIC_WALL_NAME, description: "Data questions and answers people shared on EasyQuery.", siteName: "EasyQuery" },
};

/**
 * The Insight Wall. Signed in: the organization's wall and the public one (votes, search, paging in the browser).
 * Signed out (and on the insights host, which never has the app's session): the public wall, rendered here.
 */
export default async function InsightsPage({ searchParams }: { searchParams: Record<string, string | undefined> }) {
  const identity = await currentIdentity();
  if (identity.kind === "expired") redirect("/auth/refresh");
  const appUrl = config.publicUrl.replace(/\/$/, "");
  if (identity.kind === "ok") {
    const theme = await tenantTheme();
    const org = identity.me.tenant.name ?? undefined;
    return (
      <div className="odn-app odn-app--page">
        <BrandBar theme={theme} company={org} user={identity.displayName} nav={[{ href: "/?tab=chats", label: "Chats" }]} />
        <main className="shared-main">
          <InsightWall appUrl={appUrl} signedIn organization={org} />
        </main>
      </div>
    );
  }
  const sort = isSort(searchParams.sort) ? searchParams.sort : "hot";
  const q = (searchParams.q ?? "").trim().slice(0, 100);
  const offset = Math.max(0, Number.parseInt(searchParams.offset ?? "0", 10) || 0);
  const items = await fetchPublicWall(sort, q, offset);
  return (
    <div className="odn-app odn-app--page">
      <BrandBar theme={currentTheme()} />
      <main className="shared-main">
        {items === null ? (
          <p className="muted" role="alert">The Insight Wall is unavailable right now. Please try again shortly.</p>
        ) : (
          <InsightWall appUrl={appUrl} signedIn={false} initial={{ items, sort, q, offset }} />
        )}
      </main>
    </div>
  );
}
