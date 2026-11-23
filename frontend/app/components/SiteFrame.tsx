import type { ReactNode } from "react";

import BrandBar from "./BrandBar";
import { currentTheme } from "../themes/server";
import { appUrl, supportEmail } from "@/lib/site";

/** Public pages (home, privacy): brand bar, content and a footer; no sign-in required. */
export default function SiteFrame({ children }: { children: ReactNode }) {
  const theme = currentTheme();
  const support = supportEmail();
  return (
    <div className="odn-site">
      <BrandBar theme={theme} />
      <main className="odn-site-main">{children}</main>
      <footer className="odn-site-footer">
        <span>© {new Date().getFullYear()} QueryMuse</span>
        <nav aria-label="Site">
          <a href="/">Home</a>
          <a href="/privacy">Privacy policy</a>
          <a href="/terms">Terms</a>
          <a href={`mailto:${support}`}>{support}</a>
          <a href={`${appUrl()}/signin`}>Sign in</a>
        </nav>
      </footer>
    </div>
  );
}
