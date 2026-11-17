import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

import "@copilotkit/react-ui/styles.css";
import "./themes/cotality.css";
import "./globals.css";
import { currentTheme } from "./themes/server";

export function generateMetadata(): Metadata {
  const theme = currentTheme();
  return { title: `${theme.product} | ${theme.company}` };
}

export function generateViewport(): Viewport {
  return { themeColor: currentTheme().themeColor };
}

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" data-theme={currentTheme().id}>
      <body>{children}</body>
    </html>
  );
}
