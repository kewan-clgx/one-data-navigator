-- Per-organization sign-in links (easyquery.ai/<slug>, docs/config/plan_to_easyquery.md §9): an organization's
-- slug is a top-level path, so it must not shadow an app or website path.
ALTER TABLE tenants ADD CONSTRAINT tenants_slug_not_reserved CHECK (slug NOT IN (
  'about', 'admin', 'api', 'app', 'assets', 'auth', 'blog', 'contact', 'docs', 'downloads', 'favicon', 'help',
  'home', 'insights', 'legal', 'login', 'logout', 'pricing', 'privacy', 'robots', 's', 'security', 'settings',
  'signin', 'signup', 'site', 'sitemap', 'static', 'status', 'support', 'terms', 'www'));

-- Public lookup for the sign-in link: only what the branded sign-in page shows (name, theme, sign-in method, the
-- verified email domains used as Google's hd hint). Active organizations only.
CREATE FUNCTION odn_tenant_entry(p_slug text)
RETURNS TABLE (slug text, name text, theme text, auth_method text, idp_tenant_id text, domains text[])
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT t.slug, t.name, t.theme, t.auth_method, t.idp_tenant_id,
         coalesce(array(SELECT d.domain FROM tenant_domains d
                        WHERE d.tenant_id = t.id AND d.verified_at IS NOT NULL ORDER BY d.domain), '{}')
  FROM tenants t
  WHERE t.slug = lower(p_slug) AND t.status = 'active'
$$;
REVOKE ALL ON FUNCTION odn_tenant_entry(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION odn_tenant_entry(text) TO odn_app;
