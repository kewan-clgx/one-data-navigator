"use client";

import { useState, type FormEvent } from "react";

/** Live-demo check-in: name + email, then straight into the app as a member of the demo organization. */
export default function DemoCheckIn() {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/auth/demo", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ name, email }),
      });
      if (res.ok) {
        window.location.assign("/");
        return;
      }
      const data = (await res.json().catch(() => ({}))) as { detail?: unknown };
      setError(typeof data.detail === "string" ? data.detail : "The live demo isn't available right now.");
    } catch {
      setError("Something went wrong. Please try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="odn-signin-form" onSubmit={submit}>
      <label>
        Name
        <input autoComplete="name" required maxLength={200} autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="Your name" />
      </label>
      <label>
        Email address
        <input type="email" autoComplete="email" required maxLength={320} value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@company.com" />
        <span className="odn-signin-hint">Check in with the same email within 7 days to pick up your chats.</span>
      </label>
      {error && <p className="odn-signin-error" role="alert">{error}</p>}
      <button type="submit" className="odn-btn odn-btn--lg" disabled={busy || !name.trim() || !email.trim()}>
        {busy ? "Starting…" : "Start the demo"}
      </button>
    </form>
  );
}
