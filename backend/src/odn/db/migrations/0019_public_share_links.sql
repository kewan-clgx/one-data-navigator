-- "Anyone with the link" shares (docs/chat_session_share_plan.md §11): the answer summary is readable without
-- signing in, so Teams/Slack/email previews can show it. Off unless an organization admin allows it.
ALTER TABLE tenant_sharing_settings ADD COLUMN public_links boolean NOT NULL DEFAULT false;

ALTER TABLE shared_conversations DROP CONSTRAINT shared_conversations_audience_check;
ALTER TABLE shared_conversations ADD CONSTRAINT shared_conversations_audience_check
  CHECK (audience IN ('people', 'organization', 'public'));

-- The only unauthenticated read path: a share is returned only while it is public, not revoked or expired, its
-- organization is active, and the organization still allows public links (turning that off hides them at once).
CREATE FUNCTION odn_public_share(p_id text)
RETURNS TABLE (id text, tenant_id uuid, kind text, title text, messages jsonb, artifacts jsonb,
               created_at timestamptz, expires_at timestamptz, owner_name text, organization text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT s.id, s.tenant_id, s.kind, s.title, s.messages, s.artifacts, s.created_at, s.expires_at,
         coalesce(nullif(u.display_name, ''), split_part(u.email, '@', 1)), t.name
  FROM shared_conversations s
  JOIN tenants t ON t.id = s.tenant_id AND t.status = 'active'
  JOIN users u ON u.id = s.owner_user_id
  WHERE s.id = p_id AND s.audience = 'public' AND s.revoked_at IS NULL AND s.expires_at > now()
    AND EXISTS (SELECT 1 FROM tenant_sharing_settings x
                WHERE x.tenant_id = s.tenant_id AND x.enabled AND x.public_links)
$$;
REVOKE ALL ON FUNCTION odn_public_share(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION odn_public_share(text) TO odn_app;
