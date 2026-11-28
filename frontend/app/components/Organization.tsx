"use client";

import { useCallback, useEffect, useId, useMemo, useState, useSyncExternalStore, type FormEvent, type ReactNode } from "react";

import { api } from "@/lib/client-api";
import { useSite } from "@/lib/site-context";
import { timeAgo } from "@/lib/wall";

type Role = "member" | "data_lead" | "admin";
type Org = { slug: string; name: string; auth_method: string; join_policy: string; domains: string[] };
type User = {
  id: string;
  email: string | null;
  display_name: string | null;
  role: Role;
  status: "invited" | "active" | "disabled";
  invited_at: string | null;
  last_login_at: string | null;
};
type AdminEvent = { occurred_at: string; action: string; target: string | null; actor: string | null; detail: Record<string, unknown> };

const ROLE_LABELS: Record<Role, string> = { member: "Member", data_lead: "Data lead", admin: "Admin" };
const ROLES = Object.keys(ROLE_LABELS) as Role[];
const AUTH_LABELS: Record<string, string> = {
  google: "Google sign-in (SSO)",
  password: "Email and password",
  oidc: "Single sign-on (OIDC)",
  saml: "Single sign-on (SAML)",
};

const AUTH_SHORT: Record<string, string> = { google: "Google SSO", password: "Email + password", oidc: "SSO (OIDC)", saml: "SSO (SAML)" };

const FAILURE_LABELS: Record<string, string> = {
  wrong_password_or_inactive: "wrong password or inactive account",
  too_many_attempts: "too many attempts",
  not_invited: "not invited",
  account_disabled: "account disabled",
  organization_suspended: "organization suspended",
  email_not_verified: "email not verified",
  email_not_allowed: "email not allowed",
  other_sign_in_method: "account uses another sign-in method",
};

type Sharing = { enabled: boolean; public_links: boolean; org_wall: boolean; public_wall: boolean; personal: boolean };
type OrgShare = {
  id: string; title: string; audience: string; status: string; expires_at: string; views: number; owner_name: string | null;
  owner_email: string; wall: "none" | "organization" | "public"; featured: boolean;
};
type SharingSummary = { enabled: boolean; active: number; onWall: number };

function when(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString() : "–";
}

/** "3 hours ago", with the exact time in a tooltip. */
function Ago({ iso, prefix = "" }: { iso: string | null; prefix?: string }) {
  if (!iso) return <span className="muted">Never</span>;
  return <time dateTime={iso} title={when(iso)}>{prefix}{timeAgo(iso)}</time>;
}

/** Segmented filter with counts; empty options (other than the first) are hidden. */
function Chips<T extends string>({ label, value, options, onChange }: {
  label: string; value: T; options: { id: T; label: string; count: number }[]; onChange: (v: T) => void;
}) {
  return (
    <div className="org-chips" role="group" aria-label={label}>
      {options.filter((o, i) => i === 0 || o.count > 0 || o.id === value).map((o) => (
        <button key={o.id} type="button" className="org-chip" aria-pressed={value === o.id} onClick={() => onChange(o.id)}>
          {o.label} <span className="org-chip-count">{o.count}</span>
        </button>
      ))}
    </div>
  );
}

function Switch({ checked, disabled, onChange, label }: { checked: boolean; disabled?: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <button type="button" role="switch" aria-checked={checked} aria-label={label} className="switch" disabled={disabled} onClick={() => onChange(!checked)}>
      <span aria-hidden className="switch-track"><span className="switch-thumb" /></span>
      <span className="org-switch-state">{checked ? "On" : "Off"}</span>
    </button>
  );
}

/** One sharing policy: title + explanation on the left, switch on the right; `nested` options depend on the one above. */
function PolicyRow({ title, children, checked, disabled, nested, onChange }: {
  title: string; children: ReactNode; checked: boolean; disabled?: boolean; nested?: boolean; onChange: (v: boolean) => void;
}) {
  return (
    <li className={`settings-row org-policy${nested ? " org-policy--nested" : ""}${disabled ? " org-policy--off" : ""}`}>
      <div className="settings-row-main">
        <strong>{title}</strong>
        <span className="org-policy-hint">{children}</span>
      </div>
      <Switch checked={checked} disabled={disabled} onChange={onChange} label={title} />
    </li>
  );
}

type ShareFilter = "all" | "wall" | "active" | "ended";

/** Sharing settings: on/off, whether links can be opened by anyone, the Insight Walls, and every link in the organization. */
function SharingSection({ onSummary }: { onSummary: (s: SharingSummary) => void }) {
  const { wallAddress } = useSite();
  const ids = useId();
  const [settings, setSettings] = useState<Sharing | null>(null);
  const [links, setLinks] = useState<OrgShare[]>([]);
  const [filter, setFilter] = useState<ShareFilter>("all");
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() =>
    Promise.all([api<Sharing>("admin/sharing"), api<OrgShare[]>("shares?scope=all")]).then(([s, l]) => {
      setSettings(s);
      setLinks(l);
    }), []);
  useEffect(() => {
    load().catch((e: Error) => setError(e.message));
  }, [load]);

  const active = links.filter((l) => l.status === "active");
  const onWall = active.filter((l) => l.wall !== "none");
  useEffect(() => {
    if (settings) onSummary({ enabled: settings.enabled, active: active.length, onWall: onWall.length });
  }, [settings, active.length, onWall.length, onSummary]);

  const save = (next: Sharing) =>
    api<Sharing>("admin/sharing", { method: "PUT", body: JSON.stringify(next) }).then(setSettings).catch((e: Error) => setError(e.message));
  const act = (path: string, init: RequestInit) => api(path, init).then(load).catch((e: Error) => setError(e.message));
  const revoke = (id: string) =>
    window.confirm("Revoke this link? Nobody can open it any more.") &&
    api(`shares/${id}`, { method: "DELETE" }).then(load).catch((e: Error) => setError(e.message));

  const shown = links.filter((l) =>
    filter === "all" ? true : filter === "wall" ? l.status === "active" && l.wall !== "none" : filter === "active" ? l.status === "active" : l.status !== "active");

  return (
    <section className="settings-card" aria-labelledby={`${ids}-h`}>
      <div className="settings-head">
        <h2 id={`${ids}-h`}>Sharing</h2>
        <p className="settings-hint">What people may share outside a chat, and every shared answer in the organization.</p>
      </div>
      {!settings ? (
        <p className="muted">{error ?? "Loading…"}</p>
      ) : (
        <>
          <h3>Policy</h3>
          <ul className="settings-rows">
            <PolicyRow title="Share answers as links" checked={settings.enabled} onChange={(v) => save({ ...settings, enabled: v })}>
              People can turn an answer into a link for colleagues.
            </PolicyRow>
            <PolicyRow nested title="Anyone with the link" checked={settings.public_links} disabled={!settings.enabled}
                       onChange={(v) => save({ ...settings, public_links: v })}>
              Links open without signing in; Teams, Slack and email show the question, answer and chart. Turning this off hides those links at once.
            </PolicyRow>
            {!settings.personal && (
              <PolicyRow nested title="Organization Insight Wall" checked={settings.org_wall} disabled={!settings.enabled}
                         onChange={(v) => save({ ...settings, org_wall: v })}>
                People post shared answers to one page (<a href="/insights" target="_blank" rel="noreferrer">Insights</a>) where colleagues find, upvote and follow up on them.
              </PolicyRow>
            )}
            <PolicyRow nested title="Public EasyQuery Insight Wall" checked={settings.public_wall} disabled={!settings.enabled || !settings.public_links}
                       onChange={(v) => save({ ...settings, public_wall: v })}>
              Anyone on the internet can find posted answers at {wallAddress}. Turning this off takes them down at once.
              {!settings.public_links && settings.enabled && " Needs “Anyone with the link”."}
            </PolicyRow>
          </ul>
        </>
      )}
      {error && settings && <p className="error" role="alert">{error}</p>}

      <div className="org-subhead">
        <h3>Shared answers</h3>
        {links.length > 0 && (
          <Chips label="Filter shared answers" value={filter} onChange={setFilter} options={[
            { id: "all", label: "All", count: links.length },
            { id: "active", label: "Active", count: active.length },
            { id: "wall", label: "On a wall", count: onWall.length },
            { id: "ended", label: "Revoked or expired", count: links.length - active.length },
          ]} />
        )}
      </div>
      {settings && links.length === 0 && <p className="settings-empty">Nobody has shared an answer yet.</p>}
      {shown.length > 0 && (
        <div className="org-table">
          <table className="grid">
            <thead>
              <tr>
                <th scope="col">Answer</th>
                <th scope="col">Wall</th>
                <th scope="col">Status</th>
                <th scope="col" className="org-actions-cell"><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {shown.map((l) => (
                <tr key={l.id} className={l.status === "active" ? undefined : "org-row--ended"}>
                  <td className="org-title-cell">
                    <a href={`/s/${l.id}`} target="_blank" rel="noreferrer" title={l.title}>{l.title}</a>
                    <div className="muted">
                      {l.owner_name || l.owner_email} · {l.audience === "public" ? "anyone with the link" : l.audience === "organization" ? "organization only" : "chosen people"}
                    </div>
                  </td>
                  <td>
                    {l.wall === "none" ? <span className="muted">—</span> : l.wall === "public" ? "Public" : "Organization"}
                    {l.featured && <> <span className="badge status-approved">featured</span></>}
                  </td>
                  <td>
                    {l.status === "active"
                      ? <span title={`Expires ${when(l.expires_at)}`}>until {new Date(l.expires_at).toLocaleDateString(undefined, { month: "short", day: "numeric" })}</span>
                      : <span className="badge status-deprecated">{l.status}</span>}
                  </td>
                  <td className="org-actions-cell">
                    <div className="org-actions">
                      {l.wall !== "none" && l.status === "active" && (
                        <>
                          <button type="button" className="link" onClick={() => act(`wall/${l.id}/feature`, { method: "POST", body: JSON.stringify({ featured: !l.featured }) })}>
                            {l.featured ? "Unfeature" : "Feature"}
                          </button>
                          <button type="button" className="link" onClick={() => window.confirm("Take this answer off the Insight Wall? The link keeps working.") && act(`shares/${l.id}/wall`, { method: "PUT", body: JSON.stringify({ wall: "none" }) })}>
                            Remove from wall
                          </button>
                        </>
                      )}
                      {l.status !== "revoked" && <button type="button" className="link danger" onClick={() => revoke(l.id)}>Revoke</button>}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

const subscribeOrigin = () => () => {};
const browserOrigin = () => window.location.origin;
const serverOrigin = () => "";

/** The organization's own sign-in page (/<slug>): to share in onboarding emails, the intranet or bookmarks. */
function SignInLink({ slug }: { slug: string }) {
  const origin = useSyncExternalStore(subscribeOrigin, browserOrigin, serverOrigin);
  const url = `${origin}/${slug}`;
  const [copied, setCopied] = useState(false);
  return (
    <div className="org-panel">
      <h3>Sign-in link</h3>
      <div className="org-copy">
        <a href={url} target="_blank" rel="noreferrer">{url.replace(/^https?:\/\//, "")}</a>
        <button type="button" onClick={() => navigator.clipboard.writeText(url).then(() => setCopied(true))}>
          {copied ? "Copied ✓" : "Copy"}
        </button>
      </div>
      <p className="settings-hint">Share it with your people (onboarding email, intranet): it opens your organization&apos;s sign-in directly.</p>
    </div>
  );
}

type Funnel = { visitors: number; new_visitors: number; asked: number; answered: number; returned: number; checkins: number };
type Checkin = { checked_in_at: string; name: string; email: string; kind: string; questions: number; queries: number };

const CHECKIN_KINDS: Record<string, string> = {
  new: "new",
  returning: "came back",
  fresh: "came back (new browser)",
  link: "reopened by email",
};

/** Live-demo organization: who checked in (name, email as typed, not verified) and how far they got. */
function DemoVisitorsSection({ onSummary }: { onSummary: (s: string) => void }) {
  const ids = useId();
  const [days, setDays] = useState(30);
  const [funnel, setFunnel] = useState<Funnel | null>(null);
  const [rows, setRows] = useState<Checkin[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api<Funnel>(`admin/demo/funnel?days=${days}`), api<Checkin[]>(`admin/demo/checkins?days=${days}&limit=200`)])
      .then(([f, r]) => {
        if (cancelled) return;
        setFunnel(f);
        setRows(r);
        onSummary(`${f.visitors} in ${days} days`);
      })
      .catch((e: Error) => { if (!cancelled) setError(e.message); });
    return () => { cancelled = true; };
  }, [days, onSummary]);

  const pct = (n: number) => (funnel && funnel.visitors ? `${Math.round((100 * n) / funnel.visitors)}%` : "");
  return (
    <section className="settings-card" aria-labelledby={`${ids}-demo`}>
      <div className="settings-head settings-head--action">
        <div className="settings-head">
          <h2 id={`${ids}-demo`}>Demo visitors</h2>
          <p className="settings-hint">Emails are as typed at check-in (not verified).</p>
        </div>
        <div className="org-head-tools">
          <select aria-label="Period" value={days} onChange={(e) => { setError(null); setDays(Number(e.target.value)); }}>
            <option value={7}>Last 7 days</option>
            <option value={30}>Last 30 days</option>
            <option value={90}>Last 90 days</option>
          </select>
          <a href={`/api/odn/admin/demo/checkins.csv?days=${days}`} download>Download CSV</a>
        </div>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      {funnel && (
        <div className="org-stats" aria-label="Demo funnel (people, by email)">
          <div className="org-stat"><span className="org-stat-value">{funnel.visitors}</span><span className="org-stat-label">Checked in</span>
            <span className="org-stat-sub">{funnel.new_visitors} new · {funnel.checkins} check-ins</span></div>
          <div className="org-stat"><span className="org-stat-value">{funnel.asked}</span><span className="org-stat-label">Asked a question</span><span className="org-stat-sub">{pct(funnel.asked)}</span></div>
          <div className="org-stat"><span className="org-stat-value">{funnel.answered}</span><span className="org-stat-label">Got a data answer</span><span className="org-stat-sub">{pct(funnel.answered)}</span></div>
          <div className="org-stat"><span className="org-stat-value">{funnel.returned}</span><span className="org-stat-label">Came back</span><span className="org-stat-sub">{pct(funnel.returned)}</span></div>
        </div>
      )}
      {rows && rows.length === 0 && <p className="settings-empty">No check-ins in this period.</p>}
      {rows && rows.length > 0 && (
        <div className="org-table org-table--scroll">
          <table className="grid">
            <thead>
              <tr><th>When</th><th>Name</th><th>Email</th><th>Visit</th><th className="num">Questions</th><th className="num">Queries</th></tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>
                  <td><Ago iso={r.checked_in_at} /></td>
                  <td>{r.name}</td>
                  <td><a href={`mailto:${r.email}`}>{r.email}</a></td>
                  <td>{CHECKIN_KINDS[r.kind] ?? r.kind}</td>
                  <td className="num">{r.questions}</td>
                  <td className="num">{r.queries}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function describe(e: AdminEvent): string {
  const what = e.action === "auth.failed" ? "Failed sign-in" : e.action.replace(".", " ").replace(/_/g, " ");
  return [
    what.charAt(0).toUpperCase() + what.slice(1),
    e.target ?? "",
    typeof e.detail.role === "string" ? `→ ${ROLE_LABELS[e.detail.role as Role] ?? e.detail.role}` : "",
    typeof e.detail.status === "string" ? `→ ${e.detail.status}` : "",
    typeof e.detail.reason === "string" ? `(${FAILURE_LABELS[e.detail.reason] ?? e.detail.reason.replace(/_/g, " ")})` : "",
  ].filter(Boolean).join(" ");
}

type SectionId = "overview" | "people" | "sharing" | "demo" | "activity";
const SECTION_KEY = "odn.organization.section";
const SECTION_IDS: SectionId[] = ["overview", "people", "sharing", "demo", "activity"];
const SECTION_ICONS: Record<SectionId, ReactNode> = {
  overview: <path d="M4 21V5a2 2 0 0 1 2-2h7a2 2 0 0 1 2 2v16M15 9h3a2 2 0 0 1 2 2v10M3 21h18M8 7h3M8 11h3M8 15h3" />,
  people: <path d="M16 20v-1.5a3.5 3.5 0 0 0-3.5-3.5h-5A3.5 3.5 0 0 0 4 18.5V20M10 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM20 20v-1.5a3.5 3.5 0 0 0-2.5-3.35M15.5 4.15a3.5 3.5 0 0 1 0 6.7" />,
  sharing: <path d="M10 14a4 4 0 0 0 5.66 0l3-3a4 4 0 0 0-5.66-5.66l-1 1M14 10a4 4 0 0 0-5.66 0l-3 3a4 4 0 0 0 5.66 5.66l1-1" />,
  demo: <path d="M3 20h18M6 16V10M11 16V6M16 16v-4M21 16V8" />,
  activity: <path d="M12 7v5l3 2M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z" />,
};

type PeopleFilter = "all" | User["status"];

/**
 * Organization tab (admins): one section at a time behind the same status switcher as Settings —
 * overview, people and invites, sharing, demo visitors (live-demo organization only) and admin activity.
 * Sections stay mounted (hidden) so switching keeps their state.
 */
export default function Organization({ currentUserId, demo = false }: { currentUserId?: string; demo?: boolean }) {
  const ids = useId();
  const [section, setSection] = useState<SectionId>(() => {
    const saved = typeof window === "undefined" ? null : localStorage.getItem(SECTION_KEY);
    return SECTION_IDS.includes(saved as SectionId) ? (saved as SectionId) : "overview";
  });
  const [org, setOrg] = useState<Org | null>(null);
  const [users, setUsers] = useState<User[] | null>(null);
  const [events, setEvents] = useState<AdminEvent[] | null>(null);
  const [sharing, setSharing] = useState<SharingSummary | null>(null);
  const [demoSummary, setDemoSummary] = useState("…");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("member");
  const [filter, setFilter] = useState<PeopleFilter>("all");
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  const load = useCallback(() =>
    Promise.all([
      api<Org>("admin/organization"),
      api<User[]>("admin/users"),
      api<AdminEvent[]>("admin/events?limit=20"),
    ]).then(([o, u, e]) => {
      setOrg(o);
      setUsers(u);
      setEvents(e);
    }), []);

  useEffect(() => {
    load().catch((e: Error) => setMessage({ kind: "error", text: e.message }));
  }, [load]);

  const choose = (id: SectionId) => {
    setSection(id);
    localStorage.setItem(SECTION_KEY, id);
  };

  async function run(fn: () => Promise<unknown>, ok: string) {
    setBusy(true);
    setMessage(null);
    try {
      await fn();
      await load();
      setMessage({ kind: "ok", text: ok });
    } catch (e) {
      setMessage({ kind: "error", text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  }

  const onInvite = (e: FormEvent) => {
    e.preventDefault();
    const address = email.trim();
    run(async () => {
      await api("admin/users", { method: "POST", body: JSON.stringify({ email: address, role }) });
      setEmail("");
    }, org?.auth_method === "password" ? `Invited ${address}; they will get an email to set a password.` : `Invited ${address}; they can sign in now.`);
  };

  const patch = (u: User, body: Partial<Pick<User, "role" | "status">>, ok: string) =>
    run(() => api(`admin/users/${u.id}`, { method: "PATCH", body: JSON.stringify(body) }), ok);

  const all = useMemo(() => users ?? [], [users]);
  const count = (s: User["status"]) => all.filter((u) => u.status === s).length;
  const admins = all.filter((u) => u.role === "admin" && u.status === "active");
  const q = query.trim().toLowerCase();
  const shownUsers = all.filter((u) =>
    (filter === "all" || u.status === filter) &&
    (!q || (u.email ?? "").toLowerCase().includes(q) || (u.display_name ?? "").toLowerCase().includes(q)));
  const onSharing = useCallback((s: SharingSummary) => setSharing(s), []);

  const sections: { id: SectionId; label: string; status: string }[] = [
    { id: "overview", label: "Overview", status: org ? (AUTH_SHORT[org.auth_method] ?? org.auth_method) : "…" },
    { id: "people", label: "People", status: users ? `${count("active")} active${count("invited") ? ` · ${count("invited")} invited` : ""}` : "…" },
    { id: "sharing", label: "Sharing", status: sharing ? (sharing.enabled ? `${sharing.active} shared · ${sharing.onWall} on wall` : "Off") : "…" },
    ...(demo ? [{ id: "demo" as const, label: "Demo visitors", status: demoSummary }] : []),
    { id: "activity", label: "Activity", status: events ? (events[0] ? `Last ${timeAgo(events[0].occurred_at)}` : "No changes yet") : "…" },
  ];
  const active = sections.some((x) => x.id === section) ? section : "overview";

  const stats: { label: string; value: number | string; sub: string; go: SectionId }[] = [
    { label: "Active people", value: users ? count("active") : "…", sub: `${admins.length} admin${admins.length === 1 ? "" : "s"}`, go: "people" },
    { label: "Pending invites", value: users ? count("invited") : "…", sub: count("disabled") ? `${count("disabled")} disabled` : "not signed in yet", go: "people" },
    { label: "Shared answers", value: sharing ? sharing.active : "…", sub: sharing && !sharing.enabled ? "sharing is off" : "active links", go: "sharing" },
    { label: "On Insight Wall", value: sharing ? sharing.onWall : "…", sub: "posted answers", go: "sharing" },
  ];

  return (
    <div className="settings settings--sectioned org">
      <nav className="settings-nav" aria-label="Organization sections">
        {sections.map((x) => (
          <button key={x.id} type="button" className="settings-nav-item" aria-current={active === x.id ? "page" : undefined} onClick={() => choose(x.id)}>
            <svg aria-hidden viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              {SECTION_ICONS[x.id]}
            </svg>
            <span className="settings-nav-text">
              <span className="settings-nav-label">{x.label}</span>
              <span className="settings-nav-status">{x.status}</span>
            </span>
          </button>
        ))}
      </nav>

      <div hidden={active !== "overview"}>
        <section className="settings-card" aria-labelledby={`${ids}-org`}>
          <div className="settings-head">
            <h2 id={`${ids}-org`}>{org?.name ?? "Organization"}</h2>
            <p className="settings-hint">Name, domains and sign-in method are managed by the platform team.</p>
          </div>
          <div className="org-stats">
            {stats.map((s) => (
              <button key={s.label} type="button" className="org-stat" onClick={() => choose(s.go)}>
                <span className="org-stat-value">{s.value}</span>
                <span className="org-stat-label">{s.label}</span>
                <span className="org-stat-sub">{s.sub}</span>
              </button>
            ))}
          </div>
          {org ? (
            <div className="org-overview">
              <div className="org-panel">
                <h3>Access</h3>
                <dl className="org-profile">
                  <dt>Sign-in</dt>
                  <dd>{AUTH_LABELS[org.auth_method] ?? org.auth_method}</dd>
                  <dt>Email domains</dt>
                  <dd>{org.domains.length ? org.domains.map((d) => `@${d}`).join(", ") : "Any (no domain restriction)"}</dd>
                  <dt>New people</dt>
                  <dd>
                    {org.join_policy !== "domain"
                      ? "Invite only"
                      : org.domains.length
                        ? "Anyone with a verified email on these domains joins as a member"
                        : "Anyone the sign-in provider admits joins as a member"}
                  </dd>
                  <dt>Admins</dt>
                  <dd>{admins.map((a) => a.email).join(", ") || "–"}</dd>
                </dl>
              </div>
              <SignInLink slug={org.slug} />
            </div>
          ) : (
            <p className="muted">{message?.kind === "error" ? message.text : "Loading…"}</p>
          )}
        </section>
      </div>

      <div hidden={active !== "people"}>
        <section className="settings-card" aria-labelledby={`${ids}-people`}>
          <div className="settings-head">
            <h2 id={`${ids}-people`}>People</h2>
            <p className="settings-hint">Invite colleagues, change roles, and turn access off. Data leads can manage shared data sources.</p>
          </div>
          <form onSubmit={onInvite} className="settings-form org-invite" aria-label="Invite someone">
            <input
              type="email"
              required
              aria-label="Email to invite"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder={`Invite by email — ${org?.domains[0] ? `name@${org.domains[0]}` : "name@company.com"}`}
            />
            <select aria-label="Role for the invite" value={role} onChange={(e) => setRole(e.target.value as Role)}>
              {ROLES.map((r) => <option key={r} value={r}>{ROLE_LABELS[r]}</option>)}
            </select>
            <button type="submit" disabled={busy || !email.trim()}>Send invite</button>
          </form>
          {message && <p className={message.kind === "ok" ? "ok" : "error"} role="status">{message.text}</p>}
          <div className="org-subhead">
            <Chips label="Filter people" value={filter} onChange={setFilter} options={[
              { id: "all", label: "All", count: all.length },
              { id: "active", label: "Active", count: count("active") },
              { id: "invited", label: "Invited", count: count("invited") },
              { id: "disabled", label: "Disabled", count: count("disabled") },
            ]} />
            {all.length > 5 && (
              <input type="search" className="org-search" aria-label="Search people" placeholder="Search name or email" value={query} onChange={(e) => setQuery(e.target.value)} />
            )}
          </div>
          <div className="org-table">
            <table className="grid">
              <thead>
                <tr>
                  <th scope="col">Person</th>
                  <th scope="col">Role</th>
                  <th scope="col">Last sign-in</th>
                  <th scope="col" className="org-actions-cell"><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {shownUsers.map((u) => {
                  const self = u.id === currentUserId;
                  const name = u.display_name || u.email || "?";
                  return (
                    <tr key={u.id} className={u.status === "disabled" ? "org-row--ended" : undefined}>
                      <td>
                        <div className="org-person">
                          <span aria-hidden className="org-avatar">{name.charAt(0).toUpperCase()}</span>
                          <div className="org-person-text">
                            <span>{u.display_name || u.email}{self && <span className="badge">you</span>}</span>
                            {(u.display_name || u.status !== "active") && (
                              <span className="muted">
                                {u.status !== "active" && <span className={`badge status-${u.status === "invited" ? "draft" : "deprecated"}`}>{u.status}</span>}
                                {u.display_name && u.email}
                              </span>
                            )}
                          </div>
                        </div>
                      </td>
                      <td>
                        <select
                          className="org-role"
                          aria-label={`Role of ${u.email}`}
                          value={u.role}
                          disabled={busy || self}
                          title={self ? "You can't change your own role" : undefined}
                          onChange={(e) => patch(u, { role: e.target.value as Role }, `${u.email} is now ${ROLE_LABELS[e.target.value as Role]}.`)}
                        >
                          {ROLES.map((r) => <option key={r} value={r}>{ROLE_LABELS[r]}</option>)}
                        </select>
                      </td>
                      <td>
                        {u.status === "invited" ? <span className="muted"><Ago iso={u.invited_at} prefix="Invite sent " /></span> : <Ago iso={u.last_login_at} />}
                      </td>
                      <td className="org-actions-cell">
                        <div className="org-actions">
                          {u.status === "invited" && (
                            <>
                              <button type="button" className="link" disabled={busy}
                                onClick={() => run(() => api(`admin/users/${u.id}/resend`, { method: "POST" }), `Invite sent again to ${u.email}.`)}>
                                Resend
                              </button>
                              <button type="button" className="link danger" disabled={busy}
                                onClick={() => window.confirm(`Revoke the invite for ${u.email}?`) &&
                                  run(() => api(`admin/users/${u.id}`, { method: "DELETE" }), `Invite for ${u.email} revoked.`)}>
                                Revoke
                              </button>
                            </>
                          )}
                          {u.status === "active" && !self && (
                            <button type="button" className="link danger" disabled={busy}
                              onClick={() => window.confirm(`Disable ${u.email}? They will lose access immediately.`) &&
                                patch(u, { status: "disabled" }, `${u.email} disabled.`)}>
                              Disable
                            </button>
                          )}
                          {u.status === "disabled" && (
                            <button type="button" className="link" disabled={busy} onClick={() => patch(u, { status: "active" }, `${u.email} enabled.`)}>
                              Enable
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {users && shownUsers.length === 0 && <p className="settings-empty">Nobody matches.</p>}
          </div>
        </section>
      </div>

      <div hidden={active !== "sharing"}>
        <SharingSection onSummary={onSharing} />
      </div>

      {demo && (
        <div hidden={active !== "demo"}>
          <DemoVisitorsSection onSummary={setDemoSummary} />
        </div>
      )}

      <div hidden={active !== "activity"}>
        <section className="settings-card" aria-labelledby={`${ids}-activity`}>
          <div className="settings-head">
            <h2 id={`${ids}-activity`}>Admin activity</h2>
            <p className="settings-hint">The 20 most recent admin changes and failed sign-ins.</p>
          </div>
          {events && events.length === 0 && <p className="settings-empty">No admin changes yet.</p>}
          {events && events.length > 0 && (
            <div className="org-table">
              <table className="grid">
                <thead>
                  <tr><th scope="col">When</th><th scope="col">Who</th><th scope="col">What</th></tr>
                </thead>
                <tbody>
                  {events.map((e, i) => (
                    <tr key={i} className={e.action === "auth.failed" ? "org-row--warn" : undefined}>
                      <td><Ago iso={e.occurred_at} /></td>
                      <td>{e.action === "auth.failed" ? <span className="muted">—</span> : e.actor ?? "someone"}</td>
                      <td className="org-wrap">{describe(e)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
