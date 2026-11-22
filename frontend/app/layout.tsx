import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

import "@copilotkit/react-ui/styles.css";
import "./themes/cotality.css";
import "./globals.css";
import { brandCompanyLabel } from "./themes";
import { brandCompany, tenantTheme } from "./themes/server";

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
    <html lang="en" data-theme={theme.id}>
      <body>{children}</body>
    </html>
  );
}
