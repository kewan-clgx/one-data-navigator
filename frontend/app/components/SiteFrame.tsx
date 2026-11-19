import type { ReactNode } from "react";

import BrandBar from "./BrandBar";
import { currentTheme } from "../themes/server";

export const SUPPORT_EMAIL = "support@querymuse.ai";

/** Where "Sign in" goes: the app origin (the public site may be served from another host). */
export function appUrl(): string {
  return process.env.ODN_PUBLIC_URL || "/";
}

/** Public pages (home, privacy): brand bar, content and a footer; no sign-in required. */
export default function SiteFrame({ children }: { children: ReactNode }) {
  const theme = currentTheme();
  return (
    <div className="odn-site">
      <BrandBar theme={theme} />
      <main className="odn-site-main">{children}</main>
      <footer className="odn-site-footer">
        <span>© {new Date().getFullYear()} QueryMuse</span>
        <nav aria-label="Site">
          <a href="/site">Home</a>
          <a href="/privacy">Privacy policy</a>
          <a href={`mailto:${SUPPORT_EMAIL}`}>{SUPPORT_EMAIL}</a>
          <a href={appUrl()}>Sign in</a>
        </nav>
      </footer>
    </div>
  );
}
