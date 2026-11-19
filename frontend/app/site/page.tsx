import type { Metadata } from "next";

import SiteFrame, { appUrl, SUPPORT_EMAIL } from "../components/SiteFrame";

export const metadata: Metadata = {
  title: "QueryMuse — the conversational data analytics platform",
  description:
    "QueryMuse is a conversational data analytics platform: ask questions about your company's data in plain English and get governed SQL, answers, charts and explanations.",
};

const STEPS = [
  { title: "Ask", text: "\u201cHow many active businesses do we have in Las Vegas and San Diego, by status?\u201d" },
  { title: "Analyse", text: "QueryMuse finds the right tables, writes read-only SQL, checks its cost and runs it on your data." },
  { title: "Answer", text: "Get the result as a table or chart with a plain-English explanation, and the SQL one click away." },
];

const FEATURES = [
  {
    title: "Analytics as a conversation",
    text: "Ask follow-ups the way you would ask an analyst: filter, group, compare and chart without writing SQL. Every answer shows where it came from.",
  },
  {
    title: "Fast follow-ups",
    text: "Results stay in a private workspace, so follow-up questions, charts and CSV downloads run instantly without scanning the warehouse again.",
  },
  {
    title: "Your data, where it lives",
    text: "Connect BigQuery and Postgres (including through an SSH bastion). Browse and search the catalog with source descriptions and your team's curated definitions.",
  },
  {
    title: "Governed by design",
    text: "Only allowlisted tables, read-only queries, approval before expensive scans, an audit log of every query, and strict separation between organizations.",
  },
];

export default function SiteHome() {
  return (
    <SiteFrame>
      <section className="odn-site-hero">
        <p className="odn-site-kicker">The conversational data analytics platform</p>
        <h1>Talk to your data. Get answers you can trust.</h1>
        <p className="odn-site-lead">
          QueryMuse turns plain-English questions into governed analysis. Your team asks; QueryMuse queries your
          BigQuery and Postgres data, explains the results and charts them, with no SQL required and nothing
          hidden.
        </p>
        <div className="odn-site-cta">
          <a className="odn-btn odn-btn--lg" href={appUrl()}>Sign in</a>
          <a className="link" href={`mailto:${SUPPORT_EMAIL}?subject=QueryMuse%20for%20my%20organization`}>
            Get QueryMuse for your organization
          </a>
        </div>
      </section>

      <section className="odn-site-steps" aria-label="How it works">
        {STEPS.map((s, i) => (
          <article key={s.title} className="odn-site-card">
            <p className="odn-site-step">{i + 1}</p>
            <h2>{s.title}</h2>
            <p>{s.text}</p>
          </article>
        ))}
      </section>

      <section className="odn-site-features" aria-label="Why QueryMuse">
        {FEATURES.map((f) => (
          <article key={f.title} className="odn-site-card">
            <h2>{f.title}</h2>
            <p>{f.text}</p>
          </article>
        ))}
      </section>

      <section className="odn-site-section">
        <h2>Built for organizations</h2>
        <p>
          Each organization gets its own space, connections and members. People sign in with their organization and
          work email, using their company&rsquo;s Google account or a password set from an invitation. We use your
          Google account only to confirm who you are (your name and email address); see our{" "}
          <a href="/privacy">privacy policy</a>.
        </p>
      </section>
    </SiteFrame>
  );
}
