import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import AboutSlides from "./components/AboutSlides";
import AppShell from "./components/AppShell";
import BrandBar from "./components/BrandBar";
import SignInForm from "./components/SignInForm";
import { currentTheme, tenantTheme } from "./themes/server";
import { currentIdentity } from "@/lib/identity";
import { ORG_COOKIE } from "@/lib/session";

export const dynamic = "force-dynamic";

export default async function Home() {
  const identity = await currentIdentity();
  if (identity.kind === "expired") redirect("/auth/refresh");
  // Back to the shared conversation the user opened before signing in (cookie set by /s/[id]; cleared there).
  let returnTo = "";
  try {
    returnTo = decodeURIComponent(cookies().get("odn_return")?.value ?? "");
  } catch {
    // malformed cookie: ignore
  }
  if (identity.kind === "ok" && /^\/s\/[A-Za-z0-9_-]{22}(\?followup=1)?$/.test(returnTo)) redirect(returnTo);
  if (identity.kind === "signed-out") {
    const theme = currentTheme();
    return (
      <div className="odn-app">
        <BrandBar theme={theme} />
        <main className="odn-signin">
          <section className="odn-panel odn-decor">
            <AboutSlides slides={theme.about} label={`About ${theme.product}`} />
          </section>
          <section className="odn-signin-main">
            <div className="odn-signin-card">
              <h1>Welcome to {theme.product}</h1>
              <p>Sign in with your organization and work email.</p>
              <SignInForm defaultOrganization={cookies().get(ORG_COOKIE)?.value} />
            </div>
          </section>
        </main>
      </div>
    );
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
  return (
    <div className="odn-app">
      <BrandBar theme={theme} company={identity.me.tenant.name ?? undefined} user={identity.displayName} />
      <AppShell theme={theme} role={identity.me.role} userId={identity.me.user_id} />
    </div>
  );
}
