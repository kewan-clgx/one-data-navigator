"use client";

import { useCallback, useEffect, useId, useState, type FormEvent } from "react";

import { api } from "@/lib/client-api";

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
const AUTH_LABELS: Record<string, string> = {
  google: "Google sign-in (SSO)",
  password: "Email and password",
  oidc: "Single sign-on (OIDC)",
  saml: "Single sign-on (SAML)",
};

const FAILURE_LABELS: Record<string, string> = {
  wrong_password_or_inactive: "wrong password or inactive account",
  too_many_attempts: "too many attempts",
  not_invited: "not invited",
  account_disabled: "account disabled",
  organization_suspended: "organization suspended",
  email_not_allowed: "email not allowed",
  other_sign_in_method: "account uses another sign-in method",
};

function when(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString() : "–";
}

/** Organization tab (admins): profile, people and invites, admin activity. */
export default function Organization({ currentUserId }: { currentUserId?: string }) {
  const ids = useId();
  const [org, setOrg] = useState<Org | null>(null);
  const [users, setUsers] = useState<User[]>([]);
  const [events, setEvents] = useState<AdminEvent[]>([]);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("member");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  const load = useCallback(async () => {
    const [o, u, e] = await Promise.all([
      api<Org>("admin/organization"),
      api<User[]>("admin/users"),
      api<AdminEvent[]>("admin/events?limit=20"),
    ]);
    setOrg(o);
    setUsers(u);
    setEvents(e);
  }, []);

  useEffect(() => {
    load().catch((e: Error) => setMessage({ kind: "error", text: e.message }));
  }, [load]);

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

  const admins = users.filter((u) => u.role === "admin" && u.status === "active");

  return (
    <div className="settings">
      <section className="settings-card" aria-labelledby={`${ids}-org`}>
        <h2 id={`${ids}-org`}>Organization</h2>
        {org ? (
          <dl className="org-profile">
            <dt>Name</dt>
            <dd>{org.name}</dd>
            <dt>Email domains</dt>
            <dd>{org.domains.length ? org.domains.map((d) => `@${d}`).join(", ") : "Any (no domain restriction)"}</dd>
            <dt>Sign-in</dt>
            <dd>{AUTH_LABELS[org.auth_method] ?? org.auth_method}</dd>
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
        ) : (
          <p className="muted">Loading…</p>
        )}
        <p className="muted">Name, domains and sign-in method are managed by the platform team.</p>
      </section>

      <section className="settings-card" aria-labelledby={`${ids}-people`}>
        <h2 id={`${ids}-people`}>People</h2>
        <form onSubmit={onInvite} className="settings-form org-invite">
          <label>
            Invite by email
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder={org?.domains[0] ? `name@${org.domains[0]}` : "name@company.com"}
            />
          </label>
          <label>
            Role
            <select value={role} onChange={(e) => setRole(e.target.value as Role)}>
              {(Object.keys(ROLE_LABELS) as Role[]).map((r) => (
                <option key={r} value={r}>{ROLE_LABELS[r]}</option>
              ))}
            </select>
          </label>
          <button type="submit" disabled={busy || !email.trim()}>Invite</button>
        </form>
        {message && <p className={message.kind === "ok" ? "ok" : "error"} role="status">{message.text}</p>}
        <div className="org-table">
          <table className="grid">
            <thead>
              <tr>
                <th scope="col">Person</th>
                <th scope="col">Role</th>
                <th scope="col">Status</th>
                <th scope="col">Last sign-in</th>
                <th scope="col"><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => {
                const self = u.id === currentUserId;
                return (
                  <tr key={u.id}>
                    <td>
                      {u.display_name && <div>{u.display_name}</div>}
                      <div className="muted">{u.email}{self ? " (you)" : ""}</div>
                    </td>
                    <td>
                      <select
                        aria-label={`Role of ${u.email}`}
                        value={u.role}
                        disabled={busy || self}
                        onChange={(e) => patch(u, { role: e.target.value as Role }, `${u.email} is now ${ROLE_LABELS[e.target.value as Role]}.`)}
                      >
                        {(Object.keys(ROLE_LABELS) as Role[]).map((r) => (
                          <option key={r} value={r}>{ROLE_LABELS[r]}</option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <span className={`badge status-${u.status === "active" ? "approved" : u.status === "invited" ? "draft" : "deprecated"}`}>
                        {u.status}
                      </span>
                    </td>
                    <td>{u.status === "invited" ? `invited ${when(u.invited_at)}` : when(u.last_login_at)}</td>
                    <td className="org-actions">
                      {u.status === "invited" && (
                        <>
                          <button type="button" className="link" disabled={busy}
                            onClick={() => run(() => api(`admin/users/${u.id}/resend`, { method: "POST" }), `Invite sent again to ${u.email}.`)}>
                            Resend
                          </button>
                          <button type="button" className="link" disabled={busy}
                            onClick={() => window.confirm(`Revoke the invite for ${u.email}?`) &&
                              run(() => api(`admin/users/${u.id}`, { method: "DELETE" }), `Invite for ${u.email} revoked.`)}>
                            Revoke
                          </button>
                        </>
                      )}
                      {u.status === "active" && !self && (
                        <button type="button" className="link" disabled={busy}
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
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <section className="settings-card" aria-labelledby={`${ids}-activity`}>
        <h2 id={`${ids}-activity`}>Recent admin activity</h2>
        {events.length === 0 ? (
          <p className="muted">No admin changes yet.</p>
        ) : (
          <ul className="org-events">
            {events.map((e, i) => (
              <li key={i}>
                <span className="muted">{when(e.occurred_at)}</span> {e.action === "auth.failed" ? "Failed sign-in" : `${e.actor ?? "someone"} — ${e.action.replace(".", " ").replace(/_/g, " ")}`}
                {e.target ? ` ${e.target}` : ""}
                {typeof e.detail.role === "string" ? ` → ${ROLE_LABELS[e.detail.role as Role] ?? e.detail.role}` : ""}
                {typeof e.detail.status === "string" ? ` → ${e.detail.status}` : ""}
                {typeof e.detail.reason === "string" ? ` (${FAILURE_LABELS[e.detail.reason] ?? e.detail.reason.replace(/_/g, " ")})` : ""}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
