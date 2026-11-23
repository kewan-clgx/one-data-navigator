-- Insight Wall (docs/roadmap/insight_wall_dashboards_plan.md): owners post a shared answer to their organization's
-- wall or to the public QueryMuse wall, where people find, upvote and follow up on it.
ALTER TABLE shared_conversations
  ADD COLUMN wall text NOT NULL DEFAULT 'none' CHECK (wall IN ('none', 'organization', 'public')),
  ADD COLUMN listed_at timestamptz,
  ADD COLUMN featured_at timestamptz;
-- Readers who find an item must be able to open it: the organization wall needs an organization (or public) link,
-- the public wall an "anyone with the link" one.
ALTER TABLE shared_conversations ADD CONSTRAINT shared_conversations_wall_audience CHECK (
  wall = 'none'
  OR (wall = 'organization' AND audience IN ('organization', 'public') AND listed_at IS NOT NULL)
  OR (wall = 'public' AND audience = 'public' AND listed_at IS NOT NULL));
CREATE INDEX shared_conversations_wall ON shared_conversations (wall, listed_at DESC) WHERE wall <> 'none';

-- NULL = the default for the kind of organization: the open personal tenant has no organization wall (anyone can
-- join it) and posts to the public wall; enterprise organizations have their wall on and the public wall off.
ALTER TABLE tenant_sharing_settings ADD COLUMN org_wall boolean, ADD COLUMN public_wall boolean;

-- tenant_id is the voter's organization: users of any organization may upvote public items. Counts across
-- organizations are only computed by odn_public_wall().
CREATE TABLE insight_votes (
  share_id   text NOT NULL REFERENCES shared_conversations (id) ON DELETE CASCADE,
  tenant_id  uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
  user_id    uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (share_id, user_id)
);
CREATE INDEX insight_votes_share ON insight_votes (share_id);
ALTER TABLE insight_votes ENABLE ROW LEVEL SECURITY;
ALTER TABLE insight_votes FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON insight_votes
  USING (tenant_id = odn_current_tenant()) WITH CHECK (tenant_id = odn_current_tenant());
REVOKE ALL ON insight_votes FROM odn_app;
GRANT SELECT, INSERT, DELETE ON insight_votes TO odn_app;

-- The public wall for anyone (no sign-in): only items posted to it whose link is public and active, whose
-- organization is active and still allows public links and the public wall. Card fields and counts only; the
-- caller strips owner_user_id (used for "is this mine" and no self-votes) and chart SQL before responding.
-- p_q is an ILIKE pattern (already escaped); p_sort is hot | new | top.
CREATE FUNCTION odn_public_wall(p_personal_slug text, p_id text, p_q text, p_sort text, p_limit int, p_offset int)
RETURNS TABLE (id text, kind text, title text, question text, answer text, chart jsonb, owner_user_id uuid,
               owner_name text, organization text, created_at timestamptz, listed_at timestamptz,
               expires_at timestamptz, votes bigint, follow_ups bigint, views bigint, score double precision)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT w.id, w.kind, w.title, w.question, w.answer, w.chart, w.owner_user_id, w.owner_name, w.organization,
         w.created_at, w.listed_at, w.expires_at, w.votes, w.follow_ups, w.views, w.score
  FROM (
    SELECT s.id, s.kind, s.title,
      (SELECT e.m->>'content' FROM jsonb_array_elements(s.messages) WITH ORDINALITY e(m, i)
        WHERE e.m->>'role' = 'user' AND coalesce(e.m->>'content', '') <> '' ORDER BY e.i LIMIT 1) AS question,
      (SELECT e.m->>'content' FROM jsonb_array_elements(s.messages) WITH ORDINALITY e(m, i)
        WHERE e.m->>'role' = 'assistant' AND coalesce(e.m->>'content', '') <> '' ORDER BY e.i DESC LIMIT 1) AS answer,
      (SELECT a FROM jsonb_array_elements(s.artifacts) a WHERE a->>'kind' = 'chart' LIMIT 1) AS chart,
      s.owner_user_id,
      coalesce(nullif(u.display_name, ''), split_part(u.email, '@', 1)) AS owner_name,
      CASE WHEN t.slug = p_personal_slug THEN NULL ELSE t.name END AS organization,
      s.created_at, s.listed_at, s.expires_at, c.votes, c.follow_ups, c.views,
      ((c.votes + 2 * c.follow_ups + 0.2 * c.views + 1)
        / power(extract(epoch FROM now() - s.listed_at) / 3600 + 2, 1.5))::double precision AS score
    FROM shared_conversations s
    JOIN tenants t ON t.id = s.tenant_id AND t.status = 'active'
    JOIN users u ON u.id = s.owner_user_id
    LEFT JOIN tenant_sharing_settings x ON x.tenant_id = s.tenant_id
    CROSS JOIN LATERAL (SELECT
      (SELECT count(*) FROM insight_votes v WHERE v.share_id = s.id) AS votes,
      (SELECT count(*) FROM shared_conversation_views v WHERE v.share_id = s.id AND v.continued_thread_id IS NOT NULL) AS follow_ups,
      (SELECT count(*) FROM shared_conversation_views v WHERE v.share_id = s.id) AS views) c
    WHERE s.wall = 'public' AND s.audience = 'public' AND s.revoked_at IS NULL AND s.expires_at > now()
      AND (p_id IS NULL OR s.id = p_id)
      AND coalesce(x.enabled AND x.public_links, true)
      AND coalesce(x.public_wall, t.slug = p_personal_slug, false)
  ) w
  WHERE p_q IS NULL OR w.title ILIKE p_q OR w.question ILIKE p_q
  ORDER BY CASE WHEN p_sort = 'top' THEN w.votes END DESC NULLS LAST,
           CASE WHEN p_sort = 'hot' THEN w.score END DESC NULLS LAST,
           w.listed_at DESC
  LIMIT least(greatest(p_limit, 1), 50) OFFSET greatest(p_offset, 0)
$$;
REVOKE ALL ON FUNCTION odn_public_wall(text, text, text, text, int, int) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION odn_public_wall(text, text, text, text, int, int) TO odn_app;
