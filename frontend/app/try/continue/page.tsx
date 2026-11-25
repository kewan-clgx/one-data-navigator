import type { Metadata } from "next";
import { notFound } from "next/navigation";

import BrandBar from "../../components/BrandBar";
import DemoContinue from "../../components/DemoContinue";
import { currentTheme } from "../../themes/server";
import { demoTenant } from "@/lib/site";

export const dynamic = "force-dynamic";

export function generateMetadata(): Metadata {
  return { title: `Reopen your demo | ${currentTheme().product}`, robots: { index: false, follow: false } };
}

/** Target of the "reopen your demo chats" email. */
export default function DemoContinuePage({ searchParams }: { searchParams: Record<string, string | undefined> }) {
  if (!demoTenant()) notFound();
  const theme = currentTheme();
  const token = searchParams.token ?? "";
  return (
    <div className="odn-app">
      <BrandBar theme={theme} />
      <main className="odn-signin">
        <section className="odn-signin-main" style={{ gridColumn: "1 / -1" }}>
          <div className="odn-signin-card">
            <h1>Reopen your demo</h1>
            {token ? <DemoContinue token={token} /> : <p role="alert">This link isn&apos;t valid. <a href="/try">Start the demo</a></p>}
          </div>
        </section>
      </main>
    </div>
  );
}
