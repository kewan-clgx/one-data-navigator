import { brandCompanyLabel, type Theme } from "../themes";

/** "{company} | {product}"; before sign-in there is no tenant yet, so only the product is shown. */
export default function BrandBar({ theme, company, user }: { theme: Theme; company?: string; user?: string }) {
  const shown = brandCompanyLabel(theme, company);
  return (
    <header className="odn-brandbar">
      <div className="odn-brand">
        <span aria-hidden className="odn-brand-mark" />
        {shown && (
          <>
            <span className="odn-brand-company">{shown}</span>
            <span aria-hidden className="odn-brand-sep">|</span>
          </>
        )}
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
