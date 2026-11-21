import type { Metadata } from "next";
import { notFound, redirect } from "next/navigation";

import BrandBar from "../../components/BrandBar";
import PublicShareView from "../../components/PublicShareView";
import SharedConversation from "../../components/SharedConversation";
import SignInToView from "../../components/SignInToView";
import { currentTheme, tenantTheme } from "../../themes/server";
import { currentIdentity } from "@/lib/identity";
import { fetchPublicShare } from "@/lib/public-share";
import { SHARE_ID, forReport, plainText, questionAndAnswer } from "@/lib/share";

export const dynamic = "force-dynamic";

const PRIVATE: Metadata = {
  title: "QueryMuse — shared answer",
  description: "An answer shared on QueryMuse. Sign in to view.",
  robots: { index: false, follow: false },
  openGraph: { title: "QueryMuse — shared answer", description: "Sign in to view.", siteName: "QueryMuse" },
};

// Teams/Slack fetch the link without signing in to build a preview. Only "anyone with the link" shares (allowed by
// the organization's admin) describe their content; everything else stays generic.
export async function generateMetadata({ params }: { params: { id: string } }): Promise<Metadata> {
  const share = await fetchPublicShare(params.id);
  if (!share) return PRIVATE;
  const { question, answer } = questionAndAnswer(share.messages);
  const title = question || share.title;
  const description = plainText(answer, 280) || `Shared by ${share.owner_name} on QueryMuse`;
  return {
    title: `${title} — QueryMuse`,
    description,
    robots: { index: false, follow: false },
    openGraph: { title, description, siteName: "QueryMuse", type: "article" },
    twitter: { card: "summary_large_image", title, description },
  };
}

export default async function SharedPage({ params }: { params: { id: string } }) {
  if (!SHARE_ID.test(params.id)) notFound();
  const [identity, rawShare] = await Promise.all([currentIdentity(), fetchPublicShare(params.id)]);
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
          <SignInToView path={`/s/${params.id}`} refused={identity.kind === "refused" ? identity.detail : null} />
        )}
      </div>
    );
  }
  const theme = await tenantTheme();
  return (
    <div className="odn-app odn-app--page">
      <BrandBar theme={theme} company={identity.me.tenant.name ?? undefined} user={identity.displayName} />
      <main className="shared-main">
        <SharedConversation shareId={params.id} fallback={publicShare} />
      </main>
    </div>
  );
}
