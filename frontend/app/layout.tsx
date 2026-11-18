import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

import "@copilotkit/react-ui/styles.css";
import "./themes/cotality.css";
import "./globals.css";
import { brandCompany, tenantTheme } from "./themes/server";

export async function generateMetadata(): Promise<Metadata> {
  const [theme, company] = await Promise.all([tenantTheme(), brandCompany()]);
  return { title: company ? `${company} | ${theme.product}` : theme.product };
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
