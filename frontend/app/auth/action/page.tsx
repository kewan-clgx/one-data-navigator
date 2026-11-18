import BrandBar from "../../components/BrandBar";
import SetPasswordForm from "../../components/SetPasswordForm";
import { currentTheme } from "../../themes/server";

export const dynamic = "force-dynamic";

/** Target of invite and password-reset emails (Identity Platform action links). */
export default function AuthAction({ searchParams }: { searchParams: Record<string, string | undefined> }) {
  const theme = currentTheme();
  const { mode, oobCode, tenantId } = searchParams;
  const usable = mode === "resetPassword" && oobCode && tenantId;
  return (
    <div className="odn-app">
      <BrandBar theme={theme} />
      <main className="odn-signin">
        <section className="odn-signin-main" style={{ gridColumn: "1 / -1" }}>
          <div className="odn-signin-card">
            <h1>Set your password</h1>
            {usable ? (
              <SetPasswordForm oobCode={oobCode} tenantId={tenantId} />
            ) : (
              <p role="alert">This link isn&apos;t valid. Ask your organization&apos;s admin to send a new invite.</p>
            )}
          </div>
        </section>
      </main>
    </div>
  );
}
