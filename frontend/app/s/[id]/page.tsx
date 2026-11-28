import type { Metadata } from "next";
import { notFound, redirect } from "next/navigation";

import BrandBar from "../../components/BrandBar";
import PublicShareView from "../../components/PublicShareView";
import SharedConversation from "../../components/SharedConversation";
import SignInToView from "../../components/SignInToView";
import { currentTheme, tenantTheme } from "../../themes/server";
import { currentIdentity } from "@/lib/identity";
import { fetchPublicShare } from "@/lib/public-share";
import { SHARE_ID, forReport, highlight } from "@/lib/share";

export const dynamic = "force-dynamic";

const PRIVATE: Metadata = {
  title: "EasyQuery — shared answer",
  description: "An answer shared on EasyQuery. Sign in to view.",
  robots: { index: false, follow: false },
  openGraph: { title: "EasyQuery — shared answer", description: "Sign in to view.", siteName: "EasyQuery" },
};

// Teams/Slack fetch the link without signing in to build a preview. Only "anyone with the link" shares (allowed by
// the organization's admin) describe their content; everything else stays generic.
export async function generateMetadata({ params }: { params: Promise<{ id: string }> }): Promise<Metadata> {
  const share = await fetchPublicShare((await params).id);
  if (!share) return PRIVATE;
  const title = share.title;
  const answer = [...share.messages].reverse().find((m) => m.role === "assistant")?.content ?? "";
  const description = highlight(answer, 200) || `Shared by ${share.owner_name} on EasyQuery`;
  return {
    title: `${title} — EasyQuery`,
    description,
    robots: { index: false, follow: false },
    openGraph: {
      title, description, siteName: "EasyQuery", type: "article", url: `/s/${share.id}`,
      images: [{ url: `/s/${share.id}/image`, width: 1200, height: 630, alt: title, type: "image/png" }],
    },
    twitter: { card: "summary_large_image", title, description, images: [`/s/${share.id}/image`] },
  };
}

export default async function SharedPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!SHARE_ID.test(id)) notFound();
  const [identity, rawShare] = await Promise.all([currentIdentity(), fetchPublicShare(id)]);
  const publicShare = rawShare && forReport(rawShare);
  if (identity.kind === "expired") redirect("/auth/refresh");
  if (identity.kind !== "ok") {
    const theme = currentTheme();
    return (
      <div className="odn-app odn-app--page">
        <BrandBar theme={theme} />
        {publicShare ? (
          <main className="shared-main"><PublicShareView share={publicShare} signedIn={false} /></main>
        ) : (
          <SignInToView path={`/s/${id}`} refused={identity.kind === "refused" ? identity.detail : null} />
        )}
      </div>
    );
  }
  const theme = await tenantTheme();
  return (
    <div className="odn-app odn-app--page">
      <BrandBar theme={theme} company={identity.me.tenant.name ?? undefined} user={identity.displayName} />
      <main className="shared-main">
        <SharedConversation shareId={id} fallback={publicShare} />
      </main>
    </div>
  );
}
