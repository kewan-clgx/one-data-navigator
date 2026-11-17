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
  product: "Smart Data Navigator",
  tag: "Beta",
  themeColor: "#1e1405",
  about: [
    {
      kicker: "One place",
      title: "All your data work, one chat",
      lead: "Stop switching tools. Ask questions, browse the catalog and check definitions in a single conversation.",
      points: [
        "BigQuery: browse, search and query allowlisted data",
        "Data dictionary: source descriptions plus curated definitions",
        "Bring your own connections and AI model key",
      ],
    },
    {
      kicker: "Property intelligence",
      title: "Ask the data. Get the answer.",
      lead: "Conversational analysis grounded in your warehouse, with every source shown.",
      points: [
        "Answers backed by live queries, with the SQL one click away",
        "Follow-ups reuse earlier results without re-scanning the warehouse",
        "Charts and CSV downloads from any result",
      ],
    },
    {
      kicker: "Governed by design",
      title: "Navigate data beyond bounds",
      lead: "Plain English in, governed SQL out.",
      points: [
        "Only allowlisted tables, read-only queries",
        "Expensive scans pause for your approval",
        "Pick a table in Explorer to focus the conversation on it",
      ],
    },
  ],
};

export const THEMES: Record<string, Theme> = { cotality };

export const DEFAULT_THEME_ID = "cotality";

export function resolveTheme(id: string | undefined): Theme {
  return (id && THEMES[id]) || THEMES[DEFAULT_THEME_ID];
}
