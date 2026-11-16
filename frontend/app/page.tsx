import Workspace from "./components/Workspace";
import { getSession } from "@/lib/session";

export const dynamic = "force-dynamic";

export default async function Home() {
  const session = await getSession();
  if (!session) {
    return (
      <main className="signin">
        <h1>One Data Navigator</h1>
        <p>Sign in with your organization account to continue.</p>
        <a className="button" href="/auth/login">
          Sign in
        </a>
      </main>
    );
  }
  return (
    <>
      <header className="top">
        <h1>One Data Navigator</h1>
        <div>
          <span className="muted">{session.name ?? session.email ?? "Signed in"}</span>{" "}
          <form method="post" action="/auth/logout">
            <button type="submit">Sign out</button>
          </form>
        </div>
      </header>
      <Workspace />
    </>
  );
}
