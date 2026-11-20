"use client";

/** Signed-out visitor of a share link: remember where to return after sign-in, then go to the sign-in page. */
export default function SignInToView({ path, refused }: { path: string; refused: string | null }) {
  function signIn() {
    document.cookie = `odn_return=${encodeURIComponent(path)}; Max-Age=900; Path=/; SameSite=Lax`;
    window.location.assign("/");
  }
  return (
    <main className="shared-main">
      <div className="shared-page">
        <h1>Sign in to view this conversation</h1>
        <p>
          {refused ?? "Someone shared a QueryMuse conversation with you. Sign in with your organization and work email to open it."}
        </p>
        {!refused && (
          <p><button type="button" className="odn-btn" onClick={signIn}>Sign in</button></p>
        )}
      </div>
    </main>
  );
}
