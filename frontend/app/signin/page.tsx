import type { Metadata } from "next";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import AboutSlides from "../components/AboutSlides";
import BrandBar from "../components/BrandBar";
import SignInForm from "../components/SignInForm";
import { currentTheme } from "../themes/server";
import { currentIdentity } from "@/lib/identity";
import { ORG_COOKIE } from "@/lib/session";
import { demoTenant } from "@/lib/site";

export const dynamic = "force-dynamic";

export function generateMetadata(): Metadata {
  return { title: `Sign in | ${currentTheme().product}` };
}

/** The sign-in page (organization + work email, or a personal account). Signed-in visitors go to the app. */
export default async function SignIn({ searchParams }: { searchParams: Record<string, string | undefined> }) {
  const identity = await currentIdentity();
  if (identity.kind === "expired") redirect("/auth/refresh");
  if (identity.kind !== "signed-out") redirect("/");
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
            <h1>Welcome to {theme.product}</h1>
            <SignInForm
              defaultOrganization={cookies().get(ORG_COOKIE)?.value}
              defaultAccountType={searchParams.account === "personal" ? "personal" : "enterprise"}
            />
            {demoTenant() && (
              <p className="odn-signin-switch">
                Just looking? <a href="/try">Start a live demo</a>
              </p>
            )}
            <p className="odn-signin-legal">
              <a href="/">Home</a> · <a href="/privacy">Privacy</a> · <a href="/terms">Terms</a>
            </p>
          </div>
        </section>
      </main>
    </div>
  );
}
