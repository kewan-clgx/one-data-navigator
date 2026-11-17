import type { Theme } from "../themes";

export default function BrandBar({ theme, user }: { theme: Theme; user?: string }) {
  return (
    <header className="odn-brandbar">
      <div className="odn-brand">
        <span aria-hidden className="odn-brand-mark" />
        <span className="odn-brand-company">{theme.company}</span>
        <span aria-hidden className="odn-brand-sep">|</span>
        <span className="odn-brand-product">{theme.product}</span>
      </div>
      <div className="odn-brandbar-right">
        {theme.tag && <span className="odn-tag">{theme.tag}</span>}
        {user !== undefined && (
          <>
            <span className="odn-brandbar-user" title={user}>{user}</span>
            <form method="post" action="/auth/logout">
              <button type="submit" className="odn-brandbar-btn">Sign out</button>
            </form>
          </>
        )}
      </div>
    </header>
  );
}
