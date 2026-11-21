/**
 * Enterprise themes. A theme = brand copy (here) + a CSS file that sets the semantic design tokens
 * under `[data-theme="<id>"]` (see ./cotality.css). Components only use the semantic tokens, so adding
 * a client theme means adding one entry here and one CSS file, without touching components.
 */

export type AboutSlide = {
  kicker: string;
  title: string;
  lead: string;
  points: string[];
};

export type Theme = {
  id: string;
  /** Company wordmark shown in the brand bar. */
  company: string;
  product: string;
  /** Small label next to the product name, e.g. "Beta". */
  tag?: string;
  /** Browser UI colour (mobile address bar). */
  themeColor: string;
  about: AboutSlide[];
};

const cotality: Theme = {
  id: "cotality",
  company: "cotality",
  product: "QueryMuse",
  tag: "Beta",
  themeColor: "#1e1405",
  about: [
    {
      kicker: "One place",
      title: "All your data work, one chat",
      lead: "Ask in plain English, get answers in seconds.",
      points: [
        "Connect to BigQuery, Snowflake, any database and documents",
        "Turn complex data into instant insights",
      ],
    },
    {
      kicker: "One language",
      title: "Use English for data analytics",
      lead: "Answers from your live data, with every source shown.",
      points: [
        "Every answer shows its source and logic",
        "Reports, charts and CSV downloads in one click",
      ],
    },
    {
      kicker: "Governed by design",
      title: "Perfectly built for managers and stakeholders",
      lead: "Plain English in, governed data report out.",
      points: [
        "Read-only, allowlisted data only",
        "Answers from your live data, with every source shown",
      ],
    },
  ],
};

export const THEMES: Record<string, Theme> = { cotality };

export const DEFAULT_THEME_ID = "cotality";

/** The tenant name to show next to the product, or none when it is the product itself (QueryMuse staff). */
export function brandCompanyLabel(theme: Theme, company: string | null | undefined): string | undefined {
  const name = company?.trim();
  return name && name.toLowerCase() !== theme.product.toLowerCase() ? name : undefined;
}

export function resolveTheme(id: string | undefined): Theme {
  return (id && THEMES[id]) || THEMES[DEFAULT_THEME_ID];
}
