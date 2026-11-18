-- Sign-in discovery also routes organizations that have no email domains (e.g. the platform tenant whose
-- members use personal Google accounts) by name, as long as they sign in with Google or the OIDC provider.
CREATE OR REPLACE FUNCTION odn_discover_tenant(p_org text, p_email_domain text)
RETURNS TABLE (slug text, name text, auth_method text, idp_tenant_id text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT t.slug, t.name, t.auth_method, t.idp_tenant_id
  FROM tenants t
  WHERE t.status = 'active'
    AND lower(btrim(p_org)) IN (t.slug, lower(t.name))
    AND (EXISTS (SELECT 1 FROM tenant_domains d
                 WHERE d.tenant_id = t.id AND d.verified_at IS NOT NULL AND d.domain = lower(p_email_domain))
         OR (t.auth_method IN ('google', 'oidc') AND NOT EXISTS (SELECT 1 FROM tenant_domains d WHERE d.tenant_id = t.id)))
  LIMIT 1
$$;
REVOKE ALL ON FUNCTION odn_discover_tenant(text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION odn_discover_tenant(text, text) TO odn_app;
