"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * The marketing site as a pitch deck: full-screen slides (scroll / arrow keys snap slide to slide) that hand
 * off to /signin. The current slide carries the `eq-deck` view-transition name, and /signin gives the same name
 * to its dark panel, so browsers with cross-document View Transitions morph the deck into the sign-in screen.
 * Visuals are scripted CSS/SVG illustrations (no real data).
 */

/** `demo` is the live-demo check-in page, when the deployment has one. */
type Links = { signin: string; personal: string; contact: string; support: string; year: number; demo?: string };

const SLIDES = [
  { id: "ask", label: "Ask your data anything" },
  { id: "weeks", label: "Reports in minutes" },
  { id: "evidence", label: "Every answer shows its work" },
  { id: "governed", label: "Governed by design" },
  { id: "connect", label: "Your data, where it lives" },
  { id: "managers", label: "Built for managers" },
  { id: "start", label: "Start asking" },
] as const;

const BARS = [
  { label: "CA", h: 92 },
  { label: "TX", h: 74 },
  { label: "FL", h: 61 },
  { label: "NY", h: 55 },
  { label: "GA", h: 38 },
];

function HeroDemo() {
  const [run, setRun] = useState(0);
  return (
    <figure className="eq-demo" aria-label="Illustration: a question becomes a chart with its source and SQL">
      <div className="eq-demo-window" key={run}>
        <div className="eq-demo-bar" aria-hidden>
          <span /><span /><span />
        </div>
        <p className="eq-demo-q">
          <span className="eq-type">Top 5 states with most crimes?</span>
        </p>
        <ul className="eq-demo-steps" aria-hidden>
          <li style={{ animationDelay: "1.9s" }}>Finding the right tables</li>
          <li style={{ animationDelay: "2.4s" }}>Writing read-only SQL</li>
          <li style={{ animationDelay: "2.9s" }}>Checking cost, then running it</li>
        </ul>
        <div className="eq-demo-chart" aria-hidden>
          {BARS.map((b, i) => (
            <div key={b.label} className="eq-demo-col">
              <span className="eq-demo-barfill" style={{ height: `${b.h}%`, animationDelay: `${3.5 + i * 0.12}s` }} />
              <small>{b.label}</small>
            </div>
          ))}
        </div>
        <p className="eq-demo-foot">
          <span>Counted crimes per state, highest first.</span>
          <span className="eq-demo-sql">SQL ▸</span>
        </p>
      </div>
      <figcaption>
        Illustrative example{" "}
        <button type="button" className="eq-linkbtn" onClick={() => setRun((r) => r + 1)}>
          ↻ Replay
        </button>
      </figcaption>
    </figure>
  );
}

function Timeline() {
  const old = ["Request", "Queue", "Write SQL", "Review", "Report"];
  return (
    <div className="eq-timeline" role="img" aria-label="Today a report goes request, queue, SQL, review, report and takes weeks. With EasyQuery you ask and get the answer in minutes.">
      <div className="eq-tl-row">
        <span className="eq-tl-who">Today</span>
        <div className="eq-tl-track">
          <span className="eq-tl-fill eq-tl-fill--slow" />
          <ol className="eq-tl-steps">{old.map((s) => <li key={s}>{s}</li>)}</ol>
        </div>
        <span className="eq-tl-time">Weeks</span>
      </div>
      <div className="eq-tl-row eq-tl-row--eq">
        <span className="eq-tl-who">EasyQuery</span>
        <div className="eq-tl-track">
          <span className="eq-tl-fill eq-tl-fill--fast" />
          <ol className="eq-tl-steps"><li>Ask</li><li>Answer</li></ol>
        </div>
        <span className="eq-tl-time">Minutes</span>
      </div>
    </div>
  );
}

function AnswerCard() {
  return (
    <figure className="eq-answer" aria-label="Illustration: an answer with its explanation, source and SQL">
      <p className="eq-answer-title">Crimes by state</p>
      <p className="eq-answer-explain eq-reveal">
        Counted reported crimes per state, highest first. Read directly from the warehouse.
      </p>
      <p className="eq-answer-src eq-reveal">
        Source <code>Google-Open-Data.crime_listings</code> · read-only · allowlisted
      </p>
      <details className="eq-answer-sql eq-reveal">
        <summary>SQL</summary>
        <pre>{`SELECT state, COUNT(*) AS crimes
FROM \`Google-Open-Data.crime_listings\`
GROUP BY state
ORDER BY crimes DESC
LIMIT 5`}</pre>
      </details>
      <table className="eq-answer-table eq-reveal" aria-hidden>
        <tbody>
          {BARS.slice(0, 3).map((b) => (
            <tr key={b.label}>
              <td>{b.label}</td>
              <td><span style={{ width: `${b.h}%` }} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}

function SharePreview() {
  return (
    <figure className="eq-share" aria-label="Illustration: a shared answer previewed in a team chat">
      <p className="eq-share-msg">
        <strong>Dana</strong> shared an answer
      </p>
      <div className="eq-share-card eq-reveal">
        <div className="eq-share-chart" aria-hidden>
          {BARS.map((b) => <span key={b.label} style={{ height: `${b.h}%` }} />)}
        </div>
        <div>
          <p className="eq-share-title">Crimes by state</p>
          <p className="eq-share-hl">California leads, followed by Texas and Florida.</p>
          <p className="eq-share-site">EasyQuery · shared answer</p>
        </div>
      </div>
    </figure>
  );
}

const GOVERNANCE = [
  { t: "Read-only", d: "Queries can never change your data." },
  { t: "Allowlisted", d: "Only the tables your admins approve." },
  { t: "Approval first", d: "Expensive scans wait for your OK." },
  { t: "Audited", d: "Every query is logged." },
  { t: "Separated", d: "Each organization is isolated." },
];

const CONNECTORS = [
  { t: "BigQuery", ready: true },
  { t: "PostgreSQL", ready: true },
  { t: "CSV & Excel uploads", ready: true },
  { t: "Snowflake", ready: false },
  { t: "More databases", ready: false },
  { t: "Documents", ready: false },
];

export default function LandingDeck({ links }: { links: Links }) {
  const [current, setCurrent] = useState(0);
  const [seen, setSeen] = useState<Set<number>>(() => new Set([0]));
  const refs = useRef<(HTMLElement | null)[]>([]);
  // Reveal animations only once JS runs, so the page is fully visible without it.
  const [js, setJs] = useState(false);
  useEffect(() => setJs(true), []);

  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (!e.isIntersecting) continue;
          const i = refs.current.indexOf(e.target as HTMLElement);
          if (i < 0) continue;
          setCurrent(i);
          setSeen((s) => (s.has(i) ? s : new Set(s).add(i)));
        }
      },
      { threshold: 0.55 },
    );
    refs.current.forEach((el) => el && observer.observe(el));
    return () => observer.disconnect();
  }, []);

  const goTo = useCallback((i: number) => {
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    refs.current[i]?.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
  }, []);

  const slide = (i: number, extra = "") =>
    `eq-slide ${extra}${seen.has(i) ? " is-seen" : ""}${current === i ? " is-current" : ""}`;
  const ref = (i: number) => (el: HTMLElement | null) => {
    refs.current[i] = el;
  };

  return (
    <div className={`eq-landing${js ? " eq-js" : ""}`}>
      <header className="odn-brandbar eq-header">
        <a className="odn-brand eq-brand" href="/" aria-label="EasyQuery home">
          <span aria-hidden className="odn-brand-mark" />
          <span className="odn-brand-product">
            EasyQuery<span className="odn-brand-tagline">, Your AI Data Analyst</span>
          </span>
        </a>
        <nav className="eq-header-actions" aria-label="Get started">
          {links.demo ? (
            <a className="eq-header-link" href={links.demo}>Start a live demo</a>
          ) : (
            <a className="eq-header-link" href={links.contact}>Get EasyQuery for your team</a>
          )}
          <a className="odn-btn odn-btn--small eq-header-signin" href={links.signin}>Sign in</a>
        </nav>
      </header>

      <nav className="eq-dots" aria-label="Slides">
        {SLIDES.map((s, i) => (
          <button
            key={s.id}
            type="button"
            aria-label={`${i + 1}. ${s.label}`}
            aria-current={current === i ? "true" : undefined}
            onClick={() => goTo(i)}
          />
        ))}
      </nav>

      <main>
        <section ref={ref(0)} id="ask" className={slide(0, "odn-decor eq-slide--hero")} aria-labelledby="eq-h-ask">
          <div className="eq-inner eq-split">
            <div className="eq-copy">
              <p className="eq-kicker">Your AI data analyst</p>
              <h1 id="eq-h-ask">Ask your data anything.</h1>
              <p className="eq-lead">All your data queries, one chat. Plain English in, answers in seconds.</p>
              <div className="eq-cta">
                <a className="odn-btn odn-btn--lg eq-btn-light" href={links.signin}>Sign in</a>
                {links.demo && <a className="eq-cta-link" href={links.demo}>Start a live demo →</a>}
                <a className="eq-cta-link" href={links.contact}>Get EasyQuery for your team →</a>
              </div>
            </div>
            <HeroDemo />
          </div>
          <button type="button" className="eq-next" onClick={() => goTo(1)} aria-label="Next slide">
            ↓
          </button>
        </section>

        <section ref={ref(1)} id="weeks" className={slide(1)} aria-labelledby="eq-h-weeks">
          <div className="eq-inner">
            <p className="eq-kicker">The problem</p>
            <h2 id="eq-h-weeks">Reports take weeks. They shouldn&rsquo;t.</h2>
            <p className="eq-lead">Every question becomes a ticket, a queue and a wait. EasyQuery gives managers the answer while the question still matters.</p>
            <Timeline />
            <p className="eq-payoff">Perfectly built for managers and stakeholders.</p>
          </div>
        </section>

        <section ref={ref(2)} id="evidence" className={slide(2)} aria-labelledby="eq-h-evidence">
          <div className="eq-inner eq-split">
            <div className="eq-copy">
              <p className="eq-kicker">Trust</p>
              <h2 id="eq-h-evidence">Every answer shows its work.</h2>
              <p className="eq-lead">A plain-English explanation of what was calculated, where it came from, and the SQL one click away. Nothing hidden.</p>
            </div>
            <AnswerCard />
          </div>
        </section>

        <section ref={ref(3)} id="governed" className={slide(3)} aria-labelledby="eq-h-governed">
          <div className="eq-inner">
            <p className="eq-kicker">Governed by design</p>
            <h2 id="eq-h-governed">Safe enough for your warehouse.</h2>
            <ul className="eq-tiles">
              {GOVERNANCE.map((g, i) => (
                <li key={g.t} className="eq-reveal" style={{ animationDelay: `${0.1 + i * 0.12}s` }}>
                  <h3>{g.t}</h3>
                  <p>{g.d}</p>
                </li>
              ))}
            </ul>
          </div>
        </section>

        <section ref={ref(4)} id="connect" className={slide(4)} aria-labelledby="eq-h-connect">
          <div className="eq-inner">
            <p className="eq-kicker">Connections</p>
            <h2 id="eq-h-connect">Your data, where it lives.</h2>
            <p className="eq-lead">Connect a warehouse or database, or drop in a spreadsheet and ask about it.</p>
            <ul className="eq-tiles eq-tiles--connect">
              {CONNECTORS.map((c, i) => (
                <li key={c.t} className={`eq-reveal${c.ready ? "" : " is-soon"}`} style={{ animationDelay: `${0.1 + i * 0.1}s` }}>
                  <h3>{c.t}</h3>
                  <p>{c.ready ? "Available" : "Coming soon"}</p>
                </li>
              ))}
            </ul>
          </div>
        </section>

        <section ref={ref(5)} id="managers" className={slide(5)} aria-labelledby="eq-h-managers">
          <div className="eq-inner eq-split">
            <div className="eq-copy">
              <p className="eq-kicker">Share and collaborate</p>
              <h2 id="eq-h-managers">Answers your whole team can use.</h2>
              <ul className="eq-points">
                <li>Share an answer as a link, with previews in Teams, Slack and email</li>
                <li>Publish highlights to your organization&rsquo;s Insight Wall</li>
                <li>Reports, charts and CSV downloads in one click</li>
              </ul>
            </div>
            <SharePreview />
          </div>
        </section>

        <section ref={ref(6)} id="start" className={slide(6, "odn-decor eq-slide--end")} aria-labelledby="eq-h-start">
          <div className="eq-inner eq-end">
            <h2 id="eq-h-start">Start asking.</h2>
            <p className="eq-lead">Reports in minutes, not weeks. No waiting.</p>
            <div className="eq-cta eq-cta--center">
              <a className="odn-btn odn-btn--lg eq-btn-light" href={links.signin}>Sign in</a>
              {links.demo && <a className="eq-cta-link" href={links.demo}>Start a live demo</a>}
              <a className="eq-cta-link" href={links.personal}>Create a personal account</a>
              <a className="eq-cta-link" href={links.contact}>Get EasyQuery for your team</a>
            </div>
          </div>
          <footer className="eq-footer">
            <span>© {links.year} EasyQuery</span>
            <nav aria-label="Site">
              <a href="/privacy">Privacy policy</a>
              <a href="/terms">Terms</a>
              <a href={`mailto:${links.support}`}>{links.support}</a>
            </nav>
          </footer>
        </section>
      </main>
    </div>
  );
}
