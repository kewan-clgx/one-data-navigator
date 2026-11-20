import type { Metadata } from "next";
import { notFound, redirect } from "next/navigation";

import BrandBar from "../../components/BrandBar";
import SharedConversation from "../../components/SharedConversation";
import SignInToView from "../../components/SignInToView";
import { currentTheme, tenantTheme } from "../../themes/server";
import { currentIdentity } from "@/lib/identity";

export const dynamic = "force-dynamic";

// Slack/Teams fetch links without signing in to build a preview: never reveal the title, data or people.
export const metadata: Metadata = {
  title: "QueryMuse — shared conversation",
  description: "A conversation shared on QueryMuse. Sign in to view.",
  robots: { index: false, follow: false },
  openGraph: { title: "QueryMuse — shared conversation", description: "Sign in to view.", siteName: "QueryMuse" },
};

export default async function SharedPage({ params }: { params: { id: string } }) {
  if (!/^[A-Za-z0-9_-]{22}$/.test(params.id)) notFound();
  const identity = await currentIdentity();
  if (identity.kind === "expired") redirect("/auth/refresh");
  if (identity.kind !== "ok") {
    const theme = currentTheme();
    return (
      <div className="odn-app odn-app--page">
        <BrandBar theme={theme} />
        <SignInToView path={`/s/${params.id}`} refused={identity.kind === "refused" ? identity.detail : null} />
      </div>
    );
  }
  const theme = await tenantTheme();
  return (
    <div className="odn-app odn-app--page">
      <BrandBar theme={theme} company={identity.me.tenant.name ?? undefined} user={identity.displayName} />
      <main className="shared-main">
        <SharedConversation shareId={params.id} />
      </main>
    </div>
  );
}
