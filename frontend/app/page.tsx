import AboutSlides from "./components/AboutSlides";
import AppShell from "./components/AppShell";
import BrandBar from "./components/BrandBar";
import { currentTheme } from "./themes/server";
import { getSession } from "@/lib/session";

export const dynamic = "force-dynamic";

export default async function Home() {
  const theme = currentTheme();
  const session = await getSession();
  if (!session) {
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
              <p>Sign in with your organization account to continue.</p>
              <a className="odn-btn odn-btn--lg" href="/auth/login">Sign in</a>
            </div>
          </section>
        </main>
      </div>
    );
  }
  return (
    <div className="odn-app">
      <BrandBar theme={theme} user={session.name ?? session.email ?? "Signed in"} />
      <AppShell theme={theme} />
    </div>
  );
}
