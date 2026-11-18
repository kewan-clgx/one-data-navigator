-- Sign-in discovery also returns the tenant id, so failed password sign-ins can be recorded in that
-- organization's activity log. Only organizations whose verified domain matches the email are returned.
DROP FUNCTION odn_discover_tenant(text, text);
CREATE FUNCTION odn_discover_tenant(p_org text, p_email_domain text)
RETURNS TABLE (tenant_id uuid, slug text, name text, auth_method text, idp_tenant_id text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT t.id, t.slug, t.name, t.auth_method, t.idp_tenant_id
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
