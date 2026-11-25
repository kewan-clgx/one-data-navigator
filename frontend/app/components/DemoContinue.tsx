"use client";

import { useEffect, useRef, useState } from "react";

/** Opens the demo chats an emailed link points to (posted on load, so link scanners can't use it up). */
export default function DemoContinue({ token }: { token: string }) {
  const [error, setError] = useState<string | null>(null);
  const sent = useRef(false);

  useEffect(() => {
    if (sent.current) return;
    sent.current = true;
    fetch("/auth/demo/link", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ token }) })
      .then(async (res) => {
        if (res.ok) {
          window.location.assign("/");
          return;
        }
        const data = (await res.json().catch(() => ({}))) as { detail?: unknown };
        setError(typeof data.detail === "string" ? data.detail : "This link can't be used.");
      })
      .catch(() => setError("Something went wrong. Please try again."));
  }, [token]);

  if (!error) return <p>Opening your demo chats…</p>;
  return (
    <>
      <p className="odn-signin-error" role="alert">{error}</p>
      <a className="odn-btn odn-btn--lg" href="/try">Start the demo</a>
    </>
  );
}
