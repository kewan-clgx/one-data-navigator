import type { Metadata, Viewport } from "next";
import Script from "next/script";
import type { ReactNode } from "react";

import "@copilotkit/react-ui/styles.css";
import "./themes/cotality.css";
import "./themes/appearance.css";
import "./themes/studio.css";
import "./themes/code.css";
import "./globals.css";
import { brandCompanyLabel } from "./themes";
import { brandCompany, tenantTheme } from "./themes/server";
import { HTML_PREFS_SCRIPT } from "@/lib/htmlPrefs";

export async function generateMetadata(): Promise<Metadata> {
  const [theme, tenantName] = await Promise.all([tenantTheme(), brandCompany()]);
  const company = brandCompanyLabel(theme, tenantName);
  // metadataBase: absolute link-preview image URLs (e.g. /s/<id>/opengraph-image) on the public address.
  const base = process.env.ODN_PUBLIC_URL;
  const site = theme.tagline ? `${theme.product}, ${theme.tagline}` : theme.product;
  return { title: company ? `${company} | ${site}` : site, ...(base ? { metadataBase: new URL(base) } : {}) };
}

export async function generateViewport(): Promise<Viewport> {
  return { themeColor: (await tenantTheme()).themeColor };
}

export default async function RootLayout({ children }: { children: ReactNode }) {
  const theme = await tenantTheme();
  return (
    // The pre-paint script adds data-appearance / data-code-theme before hydration, hence suppressHydrationWarning.
    <html lang="en" data-theme={theme.id} suppressHydrationWarning>
      <body>
        <Script id="odn-html-prefs" strategy="beforeInteractive">
          {HTML_PREFS_SCRIPT}
        </Script>
        {children}
      </body>
    </html>
  );
}
