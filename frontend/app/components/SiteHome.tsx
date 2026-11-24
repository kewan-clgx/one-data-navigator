import type { Metadata } from "next";

import LandingDeck from "./LandingDeck";
import { appUrl, demoTenant, supportEmail } from "@/lib/site";

export const SITE_METADATA: Metadata = {
  title: "EasyQuery — your AI data analyst",
  description:
    "Ask your company's data anything in plain English. EasyQuery writes governed, read-only SQL and answers with charts, explanations and sources in seconds.",
};

/** The marketing landing page: `/` for signed-out visitors and the public website host (`/site`). */
export default function SiteHome() {
  const support = supportEmail();
  const app = appUrl();
  return (
    <LandingDeck
      links={{
        signin: `${app}/signin`,
        personal: `${app}/signin?account=personal`,
        contact: `mailto:${support}?subject=EasyQuery%20for%20my%20organization`,
        support,
        year: new Date().getFullYear(),
        demo: demoTenant() ? `${app}/try` : undefined,
      }}
    />
  );
}
