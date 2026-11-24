import type { Metadata } from "next";
import { notFound, redirect } from "next/navigation";

import AboutSlides from "../components/AboutSlides";
import BrandBar from "../components/BrandBar";
import DemoCheckIn from "../components/DemoCheckIn";
import { currentTheme } from "../themes/server";
import { currentIdentity } from "@/lib/identity";
import { demoTenant } from "@/lib/site";

export const dynamic = "force-dynamic";

export function generateMetadata(): Metadata {
  return { title: `Start a live demo | ${currentTheme().product}` };
}

/** "Start a live demo": check in with a name and email, then use the app with Google BigQuery public data. */
export default async function TryDemo() {
  if (!demoTenant()) notFound();
  const identity = await currentIdentity();
  if (identity.kind === "expired") redirect("/auth/refresh");
  if (identity.kind === "ok") redirect("/");
  const theme = currentTheme();
  return (
    <div className="odn-app eq-signin">
      <BrandBar theme={theme} />
      <main className="odn-signin">
        <section className="odn-panel odn-decor">
          <AboutSlides slides={theme.about} label={`About ${theme.product}`} />
        </section>
        <section className="odn-signin-main">
          <div className="odn-signin-card">
            <h1>Start a live demo</h1>
            <p>
              Try {theme.product} as a member of <strong>Demo</strong>, a sample company, with Google BigQuery public
              data. No sign-up needed: just tell us who you are.
            </p>
            <DemoCheckIn />
            <p className="odn-signin-legal">
              We keep your name and email to follow up about {theme.product}. <a href="/privacy">Privacy</a> ·{" "}
              <a href="/signin">Sign in instead</a>
            </p>
          </div>
        </section>
      </main>
    </div>
  );
}
