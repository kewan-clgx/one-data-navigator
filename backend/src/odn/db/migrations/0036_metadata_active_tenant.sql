-- Earlier preview installations may have applied 0034 before this helper was added.
CREATE OR REPLACE FUNCTION odn_current_tenant_active() RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
  tenant_status text;
BEGIN
  SELECT status INTO tenant_status FROM tenants WHERE id = odn_current_tenant() FOR SHARE;
  RETURN coalesce(tenant_status = 'active', false);
END
$$;
REVOKE ALL ON FUNCTION odn_current_tenant_active() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION odn_current_tenant_active() TO odn_app;
