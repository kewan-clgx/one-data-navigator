-- Personal accounts (email + password self sign-up) join one operator-chosen tenant (ODN_PERSONAL_TENANT).
-- It qualifies only when anyone may join it: active, open join policy, no email domains, and an Identity
-- Platform tenant to create the accounts in. Otherwise sign-up stays closed (fail-closed).
CREATE FUNCTION odn_personal_tenant(p_slug text)
RETURNS TABLE (tenant_id uuid, idp_tenant_id text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT t.id, t.idp_tenant_id
  FROM tenants t
  WHERE t.slug = p_slug AND t.status = 'active' AND t.join_policy = 'domain' AND t.idp_tenant_id IS NOT NULL
    AND NOT EXISTS (SELECT 1 FROM tenant_domains d WHERE d.tenant_id = t.id)
$$;
REVOKE ALL ON FUNCTION odn_personal_tenant(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION odn_personal_tenant(text) TO odn_app;
