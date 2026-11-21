-- Answer links open without signing in by default (an admin can still turn this off in Organization → Sharing):
-- no settings row, or a row with public_links on, allows "anyone with the link" shares.
ALTER TABLE tenant_sharing_settings ALTER COLUMN public_links SET DEFAULT true;
UPDATE tenant_sharing_settings SET public_links = true;

CREATE OR REPLACE FUNCTION odn_public_share(p_id text)
RETURNS TABLE (id text, tenant_id uuid, kind text, title text, messages jsonb, artifacts jsonb,
               created_at timestamptz, expires_at timestamptz, owner_name text, organization text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT s.id, s.tenant_id, s.kind, s.title, s.messages, s.artifacts, s.created_at, s.expires_at,
         coalesce(nullif(u.display_name, ''), split_part(u.email, '@', 1)), t.name
  FROM shared_conversations s
  JOIN tenants t ON t.id = s.tenant_id AND t.status = 'active'
  JOIN users u ON u.id = s.owner_user_id
  WHERE s.id = p_id AND s.audience = 'public' AND s.revoked_at IS NULL AND s.expires_at > now()
    AND coalesce((SELECT x.enabled AND x.public_links FROM tenant_sharing_settings x WHERE x.tenant_id = s.tenant_id), true)
$$;
