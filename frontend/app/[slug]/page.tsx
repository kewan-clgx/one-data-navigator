import type { Metadata } from "next";
import { notFound, redirect } from "next/navigation";

import AboutSlides from "../components/AboutSlides";
import BrandBar from "../components/BrandBar";
import { OrganizationSignIn } from "../components/SignInForm";
import { THEMES } from "../themes";
import { currentTheme } from "../themes/server";
import { fetchEntry } from "@/lib/entry";
import { currentIdentity } from "@/lib/identity";

export const dynamic = "force-dynamic";

export async function generateMetadata({ params }: { params: { slug: string } }): Promise<Metadata> {
  const entry = await fetchEntry(params.slug);
  const product = currentTheme().product;
  return entry ? { title: `Sign in to ${entry.organization} | ${product}`, robots: { index: false, follow: false } } : {};
}

/** An organization's own sign-in link, e.g. easyquery.ai/indulge (docs/config/plan_to_easyquery.md §9.1). */
export default async function OrganizationEntry({ params }: { params: { slug: string } }) {
  const entry = await fetchEntry(params.slug);
  if (!entry) notFound();
  if (entry.personal) redirect("/signin?account=personal");
  const identity = await currentIdentity();
  if (identity.kind === "expired") redirect("/auth/refresh");
  if (identity.kind === "ok" && identity.me.tenant.slug === entry.slug) redirect("/");

  const theme = THEMES[entry.theme] ?? currentTheme();
  const other = identity.kind === "ok" ? identity.me.tenant.name || identity.me.tenant.slug : null;
  return (
    <div className="odn-app">
      <BrandBar theme={theme} company={other ?? entry.organization} user={identity.kind === "ok" ? identity.displayName : undefined} />
      <main className="odn-signin">
        <section className="odn-panel odn-decor">
          <AboutSlides slides={theme.about} label={`About ${theme.product}`} />
        </section>
        <section className="odn-signin-main">
          <div className="odn-signin-card">
            {other ? (
              <>
                <h1>You&apos;re signed in to {other}</h1>
                <p>This sign-in page is for {entry.organization}. Sign out first to use a {entry.organization} account.</p>
                <p><a className="odn-btn odn-btn--lg" href="/">Go to your workspace</a></p>
                <form method="post" action="/auth/logout">
                  <input type="hidden" name="next" value={`/${entry.slug}`} />
                  <button type="submit" className="link">Sign out and sign in to {entry.organization}</button>
                </form>
              </>
            ) : identity.kind === "refused" ? (
              <>
                <h1>No access</h1>
                <p role="alert">{identity.detail}</p>
                <form method="post" action="/auth/logout">
                  <input type="hidden" name="next" value={`/${entry.slug}`} />
                  <button type="submit" className="odn-btn odn-btn--lg">Sign in with another account</button>
                </form>
              </>
            ) : (
              <>
                <h1>Sign in to {entry.organization}</h1>
                <OrganizationSignIn slug={entry.slug} organization={entry.organization} method={entry.method} hd={entry.hd} />
                <p className="odn-signin-hint">
                  Not with {entry.organization}? <a href="/signin">Sign in another way</a>
                </p>
              </>
            )}
          </div>
        </section>
      </main>
    </div>
  );
}
