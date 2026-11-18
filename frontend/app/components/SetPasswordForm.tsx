"use client";

import { useEffect, useState, type FormEvent } from "react";

type State =
  | { kind: "checking" }
  | { kind: "invalid"; message: string }
  | { kind: "ready"; email: string }
  | { kind: "done"; email: string };

async function post(body: Record<string, string>) {
  const res = await fetch("/auth/reset", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  return { ok: res.ok, status: res.status, data: (await res.json().catch(() => ({}))) as { email?: string; detail?: string } };
}

export default function SetPasswordForm({ oobCode, tenantId }: { oobCode: string; tenantId: string }) {
  const [state, setState] = useState<State>({ kind: "checking" });
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    post({ oobCode, tenantId }).then(({ ok, data }) =>
      setState(ok && data.email ? { kind: "ready", email: data.email } : { kind: "invalid", message: data.detail ?? "This link can't be used." }),
    );
  }, [oobCode, tenantId]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (password !== confirm) return setError("The two passwords don't match.");
    setBusy(true);
    setError(null);
    try {
      const { ok, status, data } = await post({ oobCode, tenantId, newPassword: password });
      if (ok) return setState({ kind: "done", email: data.email ?? "" });
      if (status === 410) return setState({ kind: "invalid", message: data.detail ?? "This link has expired." });
      setError(data.detail ?? "The password could not be set.");
    } finally {
      setBusy(false);
    }
  };

  if (state.kind === "checking") return <p>Checking your link…</p>;
  if (state.kind === "invalid")
    return (
      <>
        <p className="odn-signin-error" role="alert">{state.message}</p>
        <p>On the sign-in page, enter your organization and email, then choose <strong>Forgot password?</strong> to get a new link.</p>
        <a className="odn-btn odn-btn--lg" href="/">Go to sign in</a>
      </>
    );
  if (state.kind === "done")
    return (
      <>
        <p className="odn-signin-notice" role="status">Your password is set for {state.email}.</p>
        <a className="odn-btn odn-btn--lg" href="/">Continue to sign in</a>
      </>
    );
  return (
    <form className="odn-signin-form" onSubmit={submit}>
      <p>For <strong>{state.email}</strong>. Use at least 12 characters, with upper- and lower-case letters and a number.</p>
      <label>
        New password
        <input type="password" autoComplete="new-password" required minLength={12} autoFocus value={password} onChange={(e) => setPassword(e.target.value)} />
      </label>
      <label>
        Confirm password
        <input type="password" autoComplete="new-password" required value={confirm} onChange={(e) => setConfirm(e.target.value)} />
      </label>
      {error && <p className="odn-signin-error" role="alert">{error}</p>}
      <button type="submit" className="odn-btn odn-btn--lg" disabled={busy || !password || !confirm}>
        {busy ? "Saving…" : "Set password"}
      </button>
    </form>
  );
}
