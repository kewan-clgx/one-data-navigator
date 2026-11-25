import type { Metadata } from "next";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import AboutSlides from "./components/AboutSlides";
import AppShell from "./components/AppShell";
import BrandBar from "./components/BrandBar";
import SiteHome, { SITE_METADATA } from "./components/SiteHome";
import { currentTheme, tenantTheme } from "./themes/server";
import { currentIdentity } from "@/lib/identity";
import { demoTenant, personalTenant, supportEmail, wallAddress } from "@/lib/site";
import { readSession } from "@/lib/session";

export const dynamic = "force-dynamic";

/** Signed out, "/" is the marketing page: give it the website's title and description. */
export async function generateMetadata(): Promise<Metadata> {
  return (await currentIdentity()).kind === "signed-out" ? SITE_METADATA : {};
}

export default async function Home({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const identity = await currentIdentity();
  if (identity.kind === "expired") redirect("/auth/refresh");
  // Back to the shared conversation the user opened before signing in (cookie set by /s/[id]; cleared there).
  let returnTo = "";
  try {
    returnTo = decodeURIComponent((await cookies()).get("odn_return")?.value ?? "");
  } catch {
    // malformed cookie: ignore
  }
  if (identity.kind === "ok" && /^\/s\/[A-Za-z0-9_-]{22}(\?followup=1)?$/.test(returnTo)) redirect(returnTo);
  // Signed out: the marketing landing page (sign-in is at /signin). Old "/?account=personal" links still work.
  if (identity.kind === "signed-out") {
    if ((await searchParams).account === "personal") redirect("/signin?account=personal");
    return <SiteHome />;
  }

  if (identity.kind === "refused") {
    const theme = currentTheme();
    return (
      <div className="odn-app">
        <BrandBar theme={theme} user={identity.displayName} />
        <main className="odn-signin">
          <section className="odn-panel odn-decor">
            <AboutSlides slides={theme.about} label={`About ${theme.product}`} />
          </section>
          <section className="odn-signin-main">
            <div className="odn-signin-card" role="alert">
              <h1>No access</h1>
              <p>{identity.detail}</p>
              <form method="post" action="/auth/logout">
                <button type="submit" className="odn-btn odn-btn--lg">Sign in with another account</button>
              </form>
            </div>
          </section>
        </main>
      </div>
    );
  }

  const theme = await tenantTheme();
  // Live-demo visitors (signed in by check-in, not by an account) get demo prompts and the upgrade banner.
  const demoOrg = Boolean(demoTenant()) && identity.me.tenant.slug === demoTenant();
  const session = await readSession();
  const visitor =
    demoOrg && session?.provider === "demo"
      ? { name: identity.me.user.name ?? "", email: session.email ?? identity.me.user.email ?? "" }
      : undefined;
  return (
    <div className="odn-app">
      <BrandBar theme={theme} company={identity.me.tenant.name ?? undefined} user={identity.displayName} />
      <AppShell
        theme={theme}
        role={identity.me.role}
        userId={identity.me.user_id}
        site={{ wallAddress: wallAddress(), supportEmail: supportEmail() }}
        demo={visitor}
        demoOrg={demoOrg}
        personal={Boolean(personalTenant()) && identity.me.tenant.slug === personalTenant()}
      />
    </div>
  );
}
