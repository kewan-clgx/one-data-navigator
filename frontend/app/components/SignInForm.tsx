"use client";

import { useState, type FormEvent } from "react";

type Step = { kind: "identify" } | { kind: "password"; organization: string };

async function post(path: string, body: unknown): Promise<{ ok: boolean; status: number; data: Record<string, unknown> }> {
  const res = await fetch(path, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  return { ok: res.ok, status: res.status, data: await res.json().catch(() => ({})) };
}

function message(data: Record<string, unknown>, fallback: string): string {
  return typeof data.detail === "string" ? data.detail : fallback;
}

/** Organization + work email, then SSO redirect or password, per the organization's sign-in method. */
function EnterpriseSignIn({ defaultOrganization }: { defaultOrganization?: string }) {
  const [organization, setOrganization] = useState(defaultOrganization ?? "");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [step, setStep] = useState<Step>({ kind: "identify" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const identify = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const { ok, data } = await post("/auth/discover", { organization, email });
      if (!ok) return setError(message(data, "Sign-in is unavailable. Please try again."));
      if (typeof data.redirect === "string") {
        window.location.assign(data.redirect);
        return;
      }
      if (data.method === "password") setStep({ kind: "password", organization: String(data.organization ?? organization) });
      else setError("This organization's sign-in method is not supported yet.");
    } finally {
      setBusy(false);
    }
  };

  const signIn = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const { ok, data } = await post("/auth/password", { organization, email, password });
      if (ok) {
        window.location.assign("/");
        return;
      }
      setPassword("");
      setError(message(data, "Sign-in failed."));
    } finally {
      setBusy(false);
    }
  };

  const forgot = async () => {
    setBusy(true);
    setError(null);
    try {
      const { data } = await post("/auth/forgot", { organization, email });
      setNotice(message(data, "If the account exists, a reset link is on its way."));
    } finally {
      setBusy(false);
    }
  };

  if (step.kind === "password") {
    return (
      <form className="odn-signin-form" onSubmit={signIn}>
        <p className="odn-signin-who">
          <strong>{step.organization}</strong>
          <span>{email}</span>
          <button type="button" className="link" onClick={() => { setStep({ kind: "identify" }); setPassword(""); setError(null); setNotice(null); }}>
            Change
          </button>
        </p>
        <label>
          Password
          <input type="password" autoComplete="current-password" required autoFocus value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        {error && <p className="odn-signin-error" role="alert">{error}</p>}
        {notice && <p className="odn-signin-notice" role="status">{notice}</p>}
        <button type="submit" className="odn-btn odn-btn--lg" disabled={busy || !password}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
        <button type="button" className="link" onClick={forgot} disabled={busy}>
          Forgot password?
        </button>
      </form>
    );
  }

  return (
    <form className="odn-signin-form" onSubmit={identify}>
      <p>Sign in with your organization and work email.</p>
      <label>
        Organization
        <input autoComplete="organization" required value={organization} onChange={(e) => setOrganization(e.target.value)} placeholder="Company name" />
      </label>
      <label>
        Work email
        <input type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@company.com" autoFocus={Boolean(defaultOrganization)} />
      </label>
      {error && <p className="odn-signin-error" role="alert">{error}</p>}
      <button type="submit" className="odn-btn odn-btn--lg" disabled={busy || !organization.trim() || !email.trim()}>
        {busy ? "Checking…" : "Continue"}
      </button>
    </form>
  );
}

type AccountType = "enterprise" | "personal";
type PersonalView = "sign-in" | "create" | "forgot";
type PersonalPrefill = { name: string; email: string };


function GoogleIcon() {
  return (
    <svg aria-hidden="true" width="18" height="18" viewBox="0 0 48 48">
      <path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.9 1.2 8 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z" />
      <path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.9 1.2 8 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z" />
      <path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-8l-6.5 5C9.5 39.6 16.2 44 24 44z" />
      <path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.4-.4-3.5z" />
    </svg>
  );
}

function PasswordField({
  label,
  value,
  onChange,
  autoComplete,
  hint,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  autoComplete: string;
  hint?: string;
}) {
  const [shown, setShown] = useState(false);
  return (
    <label>
      {label}
      <span className="odn-password">
        <input
          type={shown ? "text" : "password"}
          autoComplete={autoComplete}
          required
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
        <button
          type="button"
          className="odn-password-toggle"
          aria-label={shown ? "Hide password" : "Show password"}
          aria-pressed={shown}
          onClick={() => setShown((v) => !v)}
        >
          {shown ? "Hide" : "Show"}
        </button>
      </span>
      {hint && <span className="odn-signin-hint">{hint}</span>}
    </label>
  );
}

/** Personal accounts: Google (OIDC redirect), or email + password (sign in, create account, forgot password). */
function PersonalSignIn({ prefill }: { prefill?: PersonalPrefill }) {
  const [view, setView] = useState<PersonalView>(prefill ? "create" : "sign-in");
  const [name, setName] = useState(prefill?.name ?? "");
  const [email, setEmail] = useState(prefill?.email ?? "");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const go = (next: PersonalView) => {
    setView(next);
    setPassword("");
    setError(null);
    setNotice(null);
  };
  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await action();
    } catch {
      setError("Something went wrong. Please try again.");
    } finally {
      setBusy(false);
    }
  };

  const google = () => window.location.assign("/auth/login?prompt=select_account");

  const forgot = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      const { ok, data } = await post("/auth/personal/forgot", { email });
      if (ok) setNotice(message(data, "If the account exists, a reset link is on its way."));
      else setError(message(data, "Password reset is unavailable."));
    });
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    void run(async () => {
      if (view === "create") {
        const { ok, data } = await post("/auth/personal/signup", { email, name });
        if (!ok) return setError(message(data, "Sign-up is unavailable."));
        setView("sign-in");
        setNotice(message(data, "Check your inbox: we've sent a link to set your password."));
        return;
      }
      const { ok, status, data } = await post("/auth/personal/password", { email, password });
      if (ok) {
        window.location.assign("/");
        return;
      }
      setPassword("");
      if (status === 403) setNotice(message(data, "Finish creating your account with the link we emailed you."));
      else setError(message(data, "Sign-in failed."));
    });
  };

  if (view === "forgot") {
    return (
      <form className="odn-signin-form" onSubmit={forgot}>
        <p>Enter your account email and we&apos;ll send you a link to reset your password.</p>
        <label>
          Email address
          <input type="email" autoComplete="email" required autoFocus value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" />
        </label>
        {error && <p className="odn-signin-error" role="alert">{error}</p>}
        {notice && <p className="odn-signin-notice" role="status">{notice}</p>}
        <button type="submit" className="odn-btn odn-btn--lg" disabled={busy || !email.trim()}>
          {busy ? "Sending…" : "Send reset link"}
        </button>
        <button type="button" className="link" onClick={() => go("sign-in")}>
          Back to sign in
        </button>
      </form>
    );
  }

  const creating = view === "create";
  return (
    <div className="odn-signin-form">
      <p>{creating ? "Create a personal account." : "Sign in with your personal account."}</p>
      <button type="button" className="odn-btn odn-btn--lg odn-btn--outline odn-btn--provider" onClick={google} disabled={busy}>
        <GoogleIcon />
        Continue with Google
      </button>
      <div className="odn-signin-or" role="separator">
        <span>or</span>
      </div>
      <form className="odn-signin-form" onSubmit={submit}>
        {creating && (
          <label>
            Name
            <input autoComplete="name" maxLength={200} value={name} onChange={(e) => setName(e.target.value)} placeholder="Your name" />
          </label>
        )}
        <label>
          Email address
          <input type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" />
        </label>
        {creating ? (
          <p className="odn-signin-hint">We&apos;ll email you a link to set your password.</p>
        ) : (
          <>
            <PasswordField label="Password" value={password} onChange={setPassword} autoComplete="current-password" />
            <button type="button" className="link odn-signin-forgot" onClick={() => go("forgot")}>
              Forgot password?
            </button>
          </>
        )}
        {error && <p className="odn-signin-error" role="alert">{error}</p>}
        {notice && <p className="odn-signin-notice" role="status">{notice}</p>}
        <button type="submit" className="odn-btn odn-btn--lg" disabled={busy || !email.trim() || (!creating && !password)}>
          {busy ? (creating ? "Creating account…" : "Signing in…") : creating ? "Create account" : "Sign in"}
        </button>
      </form>
      <p className="odn-signin-switch">
        {creating ? "Already have an account?" : "New to personal accounts?"}{" "}
        <button type="button" className="link" onClick={() => go(creating ? "sign-in" : "create")}>
          {creating ? "Sign in" : "Create an account"}
        </button>
      </p>
    </div>
  );
}

type EntryProps = { slug: string; organization: string; method: "google" | "oidc" | "password"; hd: string | null };

/** One organization's own sign-in link (/<slug>): the organization is fixed and its sign-in method shown directly. */
export function OrganizationSignIn({ slug, organization, method, hd }: EntryProps) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  if (method !== "password") {
    const q = new URLSearchParams({ org: slug });
    if (hd) q.set("hd", hd);
    return (
      <div className="odn-signin-form">
        <a className="odn-btn odn-btn--lg odn-btn--provider" href={`/auth/login?${q}`}>
          {method === "google" && <GoogleIcon />}
          {method === "google" ? "Continue with Google" : "Continue"}
        </a>
        <p className="odn-signin-hint">Use your {organization} work account{hd ? ` (@${hd})` : ""}.</p>
      </div>
    );
  }

  const signIn = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const { ok, data } = await post("/auth/password", { organization: slug, email, password });
      if (ok) {
        window.location.assign("/");
        return;
      }
      setPassword("");
      setError(message(data, "Sign-in failed."));
    } finally {
      setBusy(false);
    }
  };
  const forgot = async () => {
    if (!email.trim()) return setError("Enter your work email first.");
    setBusy(true);
    setError(null);
    try {
      const { data } = await post("/auth/forgot", { organization: slug, email });
      setNotice(message(data, "If the account exists, a reset link is on its way."));
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="odn-signin-form" onSubmit={signIn}>
      <label>
        Work email
        <input type="email" autoComplete="email" required autoFocus value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@company.com" />
      </label>
      <PasswordField label="Password" value={password} onChange={setPassword} autoComplete="current-password" />
      {error && <p className="odn-signin-error" role="alert">{error}</p>}
      {notice && <p className="odn-signin-notice" role="status">{notice}</p>}
      <button type="submit" className="odn-btn odn-btn--lg" disabled={busy || !email.trim() || !password}>
        {busy ? "Signing in…" : "Sign in"}
      </button>
      <button type="button" className="link" onClick={forgot} disabled={busy}>
        Forgot password?
      </button>
    </form>
  );
}

/** Account-type switch (enterprise by default) above the matching sign-in form. */
export default function SignInForm({
  defaultOrganization,
  defaultAccountType = "enterprise",
  personalPrefill,
}: {
  defaultOrganization?: string;
  defaultAccountType?: AccountType;
  /** Opens "Create account" with these values (the live demo's upgrade link). */
  personalPrefill?: PersonalPrefill;
}) {
  const [type, setType] = useState<AccountType>(defaultAccountType);
  const options: { id: AccountType; label: string }[] = [
    { id: "enterprise", label: "Enterprise account" },
    { id: "personal", label: "Personal account" },
  ];
  return (
    <>
      <div className="odn-segmented" role="radiogroup" aria-label="Account type">
        {options.map((o) => (
          <button
            key={o.id}
            type="button"
            role="radio"
            aria-checked={type === o.id}
            className="odn-segmented-option"
            onClick={() => setType(o.id)}
          >
            {o.label}
          </button>
        ))}
      </div>
      {type === "enterprise" ? <EnterpriseSignIn defaultOrganization={defaultOrganization} /> : <PersonalSignIn prefill={personalPrefill} />}
    </>
  );
}
