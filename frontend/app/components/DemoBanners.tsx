"use client";

import { useEffect, useState } from "react";

import { useSite } from "@/lib/site-context";

/** Who the demo visitor checked in as (prefills the upgrade forms). */
export type DemoVisitor = { name: string; email: string };

/** Starter questions for the live demo: the curated Demo store dataset first, then public data. */
export const DEMO_SUGGESTIONS = [
  { title: "Top products by revenue", message: "Which 10 products brought in the most revenue in the Demo store? Show a chart." },
  { title: "Monthly revenue trend", message: "Show the Demo store's monthly revenue and number of orders over the last 12 months." },
  { title: "Where customers come from", message: "Which countries do the Demo store's customers come from, and how much do they spend?" },
  { title: "Returns by category", message: "Which product categories have the highest return rate in the Demo store?" },
  { title: "Public data: baby names", message: "What were the 5 most popular US baby names in 2020 (bigquery-public-data.usa_names)?" },
];

export const DEMO_PLACEHOLDER =
  "Ask about the Demo store (orders, products, customers) or public data, e.g. “Which products sold best last month?”";

/** "Keep using EasyQuery": a free personal account (prefilled) or a conversation with sales. */
export function DemoUpgradeBanner({ visitor }: { visitor: DemoVisitor }) {
  const { supportEmail } = useSite();
  const next = `/signin?${new URLSearchParams({ account: "personal", create: "1", name: visitor.name, email: visitor.email })}`;
  const body = `Hi, I tried the EasyQuery demo and would like to talk about EasyQuery for my team.\n\n${visitor.name} <${visitor.email}>`;
  const sales = `mailto:${supportEmail}?${new URLSearchParams({ subject: "EasyQuery for my team", body }).toString().replace(/\+/g, "%20")}`;
  return (
    <div className="trial-banner demo-banner" role="status">
      You&apos;re in the EasyQuery live demo. Keep using EasyQuery:{" "}
      <form method="post" action="/auth/logout" className="demo-banner-form">
        <input type="hidden" name="next" value={next} />
        <button type="submit" className="link">Create your free personal account</button>
      </form>{" "}
      (bring these chats along) or <a href={sales}>talk to sales</a>.
    </div>
  );
}

type DemoChats = { count: number; titles: string[] };

/** Personal accounts: offer to import the chats from a live demo checked in with the same (verified) email. */
export function DemoImportBanner() {
  const [chats, setChats] = useState<DemoChats | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (window.localStorage.getItem("odn.demoImport.dismissed")) return;
    fetch("/api/agent/demo-chats", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d: DemoChats | null) => setChats(d && d.count > 0 ? d : null))
      .catch(() => setChats(null));
  }, []);

  if (!chats) return null;
  const importNow = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/agent/demo-chats/import", { method: "POST" });
      if (!res.ok) throw new Error();
      window.location.reload();
    } catch {
      setError("The import didn't work. Please try again.");
      setBusy(false);
    }
  };
  const dismiss = () => {
    window.localStorage.setItem("odn.demoImport.dismissed", "1");
    setChats(null);
  };
  const preview = chats.titles.slice(0, 3).map((t) => `“${t}”`).join(", ");
  return (
    <div className="trial-banner demo-banner" role="status">
      We found {chats.count} chat{chats.count === 1 ? "" : "s"} from your EasyQuery demo{preview ? ` (${preview}${chats.count > 3 ? ", …" : ""})` : ""}.{" "}
      <button type="button" className="link" onClick={importNow} disabled={busy}>
        {busy ? "Importing…" : "Import them"}
      </button>{" "}
      ·{" "}
      <button type="button" className="link" onClick={dismiss} disabled={busy}>
        Not now
      </button>
      {error && <span role="alert"> {error}</span>}
    </div>
  );
}
