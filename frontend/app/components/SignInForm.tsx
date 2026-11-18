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
export default function SignInForm({ defaultOrganization }: { defaultOrganization?: string }) {
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
      <label>
        Organization
        <input autoComplete="organization" required value={organization} onChange={(e) => setOrganization(e.target.value)} placeholder="e.g. Cotality" />
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
