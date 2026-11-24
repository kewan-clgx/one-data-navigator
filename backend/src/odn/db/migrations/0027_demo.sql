-- Live demo (ODN_DEMO_TENANT): visitors check in with a name and email (not verified) and get a demo user in a
-- mock enterprise tenant. The API signs their tokens (issuer urn:querymuse:demo, mapped like any other issuer
-- through tenant_identity_providers). Also: a per-tenant query scan cap, and password tenants without email
-- domains (the demo tenant's admins) become discoverable on the enterprise sign-in form.

ALTER TABLE tenants ADD COLUMN max_bytes_billed bigint CHECK (max_bytes_billed > 0);

-- Every check-in (name and email as typed); demo users themselves are ordinary rows in users.
CREATE TABLE demo_checkins (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id     uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
  user_id       uuid REFERENCES users (id) ON DELETE SET NULL,
  name          text NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
  email         text NOT NULL CHECK (length(email) BETWEEN 3 AND 320),
  checked_in_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX demo_checkins_tenant_time ON demo_checkins (tenant_id, checked_in_at DESC);

ALTER TABLE demo_checkins ENABLE ROW LEVEL SECURITY;
ALTER TABLE demo_checkins FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON demo_checkins
  USING (tenant_id = odn_current_tenant()) WITH CHECK (tenant_id = odn_current_tenant());
REVOKE ALL ON demo_checkins FROM odn_app;
GRANT SELECT, INSERT ON demo_checkins TO odn_app;

-- The demo tenant, if the demo issuer maps to it and it is active.
CREATE FUNCTION odn_demo_tenant(p_issuer text, p_slug text)
RETURNS TABLE (tenant_id uuid)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT t.id
  FROM tenant_identity_providers p JOIN tenants t ON t.id = p.tenant_id
  WHERE p.issuer = p_issuer AND p.external_id = p_slug AND t.slug = p_slug AND t.status = 'active'
$$;
REVOKE ALL ON FUNCTION odn_demo_tenant(text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION odn_demo_tenant(text, text) TO odn_app;

-- Token -> tenant (0009), now also returning the tenant's query scan cap.
DROP FUNCTION odn_resolve_identity(text, text, text);
CREATE FUNCTION odn_resolve_identity(p_issuer text, p_external_id text, p_email_domain text)
RETURNS TABLE (tenant_id uuid, slug text, status text, join_policy text, fallback boolean, has_domains boolean,
               domain_ok boolean, max_bytes_billed bigint)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT t.id, t.slug, t.status, t.join_policy, p.external_id = '*',
         EXISTS (SELECT 1 FROM tenant_domains d WHERE d.tenant_id = t.id),
         NOT EXISTS (SELECT 1 FROM tenant_domains d WHERE d.tenant_id = t.id)
         OR EXISTS (SELECT 1 FROM tenant_domains d
                    WHERE d.tenant_id = t.id AND d.verified_at IS NOT NULL AND d.domain = lower(p_email_domain)),
         t.max_bytes_billed
  FROM tenant_identity_providers p JOIN tenants t ON t.id = p.tenant_id
  WHERE p.issuer = p_issuer AND p.external_id IN (lower(p_external_id), p_external_id, '*')
  ORDER BY p.external_id = '*'
  LIMIT 1
$$;
REVOKE ALL ON FUNCTION odn_resolve_identity(text, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION odn_resolve_identity(text, text, text) TO odn_app;

-- Sign-in discovery (0013) also finds domain-less password tenants by name: only accounts that already exist in
-- their Identity Platform tenant (invited admins) can sign in there.
CREATE OR REPLACE FUNCTION odn_discover_tenant(p_org text, p_email_domain text)
RETURNS TABLE (tenant_id uuid, slug text, name text, auth_method text, idp_tenant_id text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT t.id, t.slug, t.name, t.auth_method, t.idp_tenant_id
  FROM tenants t
  WHERE t.status = 'active'
    AND lower(btrim(p_org)) IN (t.slug, lower(t.name))
    AND (EXISTS (SELECT 1 FROM tenant_domains d
                 WHERE d.tenant_id = t.id AND d.verified_at IS NOT NULL AND d.domain = lower(p_email_domain))
         OR ((t.auth_method IN ('google', 'oidc') OR (t.auth_method = 'password' AND t.idp_tenant_id IS NOT NULL))
             AND NOT EXISTS (SELECT 1 FROM tenant_domains d WHERE d.tenant_id = t.id)))
  LIMIT 1
$$;

-- /try is the demo check-in page, so no organization may take it as its sign-in link (0025).
ALTER TABLE tenants DROP CONSTRAINT tenants_slug_not_reserved;
ALTER TABLE tenants ADD CONSTRAINT tenants_slug_not_reserved CHECK (slug NOT IN (
  'about', 'admin', 'api', 'app', 'assets', 'auth', 'blog', 'contact', 'docs', 'downloads', 'favicon', 'help',
  'home', 'insights', 'legal', 'login', 'logout', 'pricing', 'privacy', 'robots', 's', 'security', 'settings',
  'signin', 'signup', 'site', 'sitemap', 'static', 'status', 'support', 'terms', 'try', 'www'));
