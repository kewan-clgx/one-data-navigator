import { brandCompanyLabel, type Theme } from "../themes";

/** "{company} | {product}"; before sign-in there is no tenant yet, so only the product is shown. */
export type NavLink = { href: string; label: string; current?: boolean };

/** `nav`: links back into the app from standalone pages (e.g. the Insight Wall). */
export default function BrandBar({ theme, company, user, nav }: { theme: Theme; company?: string; user?: string; nav?: NavLink[] }) {
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
        <span className="odn-brand-product">
          {theme.product}
          {theme.tagline && <span className="odn-brand-tagline">, {theme.tagline}</span>}
        </span>
      </div>
      <div className="odn-brandbar-right">
        {nav && nav.length > 0 && (
          <nav aria-label="EasyQuery" className="odn-brandbar-nav">
            {nav.map((l) => (
              <a key={l.href} href={l.href} className="odn-brandbar-link" aria-current={l.current ? "page" : undefined}>{l.label}</a>
            ))}
          </nav>
        )}
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
