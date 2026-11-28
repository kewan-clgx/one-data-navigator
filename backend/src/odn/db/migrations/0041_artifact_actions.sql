-- Artifact delete / archive / share (docs/artifact_actions_plan.md).
--
-- Archive: an archived artifact is hidden from the Artifacts list and search but kept; purge_at (NULL = keep
-- forever) is when odn_purge_archived_artifacts() deletes it for good. Delete removes the row at once.
-- Share: one artifact as a share of kind 'artifact' (a frozen copy, the same links, audiences and expiry as answers).

ALTER TABLE workspace_artifacts ADD COLUMN archived_at timestamptz, ADD COLUMN purge_at timestamptz;
ALTER TABLE workspace_charts    ADD COLUMN archived_at timestamptz, ADD COLUMN purge_at timestamptz;
ALTER TABLE workspace_files     ADD COLUMN archived_at timestamptz, ADD COLUMN purge_at timestamptz;
ALTER TABLE saved_queries       ADD COLUMN archived_at timestamptz, ADD COLUMN purge_at timestamptz;
CREATE INDEX workspace_artifacts_purge ON workspace_artifacts (purge_at) WHERE purge_at IS NOT NULL;
CREATE INDEX workspace_charts_purge ON workspace_charts (purge_at) WHERE purge_at IS NOT NULL;
CREATE INDEX workspace_files_purge ON workspace_files (purge_at) WHERE purge_at IS NOT NULL;
CREATE INDEX saved_queries_purge ON saved_queries (purge_at) WHERE purge_at IS NOT NULL;

-- Result tables were insert-only with a tenant-wide policy. Reads stay tenant-scoped (sharing copies the owner's
-- rows), but archiving and deleting are the owner's alone.
DROP POLICY tenant_isolation ON workspace_artifacts;
CREATE POLICY tenant_read ON workspace_artifacts FOR SELECT USING (tenant_id = odn_current_tenant());
CREATE POLICY tenant_insert ON workspace_artifacts FOR INSERT WITH CHECK (tenant_id = odn_current_tenant());
CREATE POLICY owner_update ON workspace_artifacts FOR UPDATE
  USING (tenant_id = odn_current_tenant() AND user_id = odn_current_user())
  WITH CHECK (tenant_id = odn_current_tenant() AND user_id = odn_current_user());
CREATE POLICY owner_delete ON workspace_artifacts FOR DELETE
  USING (tenant_id = odn_current_tenant() AND user_id = odn_current_user());
GRANT UPDATE (archived_at, purge_at), DELETE ON workspace_artifacts TO odn_app;
GRANT UPDATE (archived_at, purge_at), DELETE ON workspace_charts, workspace_files TO odn_app;

-- Deleted q/c numbers are never handed out again in that conversation, so earlier messages that mention q3 keep
-- meaning the table that was deleted rather than a newer one.
CREATE TABLE workspace_name_floor (
  tenant_id   uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
  user_id     uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
  session_id  text NOT NULL CHECK (session_id ~ '^[A-Za-z0-9._:-]{1,200}$'),
  prefix      text NOT NULL CHECK (prefix IN ('q', 'c')),
  last_n      integer NOT NULL CHECK (last_n >= 0),
  PRIMARY KEY (tenant_id, user_id, session_id, prefix)
);
ALTER TABLE workspace_name_floor ENABLE ROW LEVEL SECURITY;
ALTER TABLE workspace_name_floor FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_only ON workspace_name_floor
  USING (tenant_id = odn_current_tenant() AND user_id = odn_current_user())
  WITH CHECK (tenant_id = odn_current_tenant() AND user_id = odn_current_user());
REVOKE ALL ON workspace_name_floor FROM odn_app;
GRANT SELECT, INSERT, UPDATE ON workspace_name_floor TO odn_app;

ALTER TABLE shared_conversations DROP CONSTRAINT shared_conversations_kind_check;
ALTER TABLE shared_conversations ADD CONSTRAINT shared_conversations_kind_check
  CHECK (kind IN ('conversation', 'answer', 'artifact'));

-- The background purge (no tenant context): deletes archived artifacts whose time is up, across all tenants, and
-- returns the storage objects to remove. Nothing else; the API's own reads and writes stay under RLS.
CREATE FUNCTION odn_purge_archived_artifacts(p_limit integer DEFAULT 500)
RETURNS TABLE (kind text, tenant_id uuid, object_key text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
  RETURN QUERY
  WITH t AS (
    DELETE FROM workspace_artifacts w WHERE w.id IN (
      SELECT x.id FROM workspace_artifacts x WHERE x.purge_at <= now() LIMIT p_limit)
    RETURNING w.tenant_id, w.user_id, w.session_id, w.name, w.object_key
  ), tf AS (
    INSERT INTO workspace_name_floor AS f (tenant_id, user_id, session_id, prefix, last_n)
    SELECT t.tenant_id, t.user_id, t.session_id, 'q', max(substring(t.name from 2)::int) FROM t
    GROUP BY t.tenant_id, t.user_id, t.session_id
    ON CONFLICT ON CONSTRAINT workspace_name_floor_pkey DO UPDATE SET last_n = greatest(f.last_n, EXCLUDED.last_n)
    RETURNING 1
  )
  SELECT 'table'::text, t.tenant_id, t.object_key FROM t;

  RETURN QUERY
  WITH c AS (
    DELETE FROM workspace_charts w WHERE w.id IN (
      SELECT x.id FROM workspace_charts x WHERE x.purge_at <= now() LIMIT p_limit)
    RETURNING w.tenant_id, w.user_id, w.session_id, w.name
  ), cf AS (
    INSERT INTO workspace_name_floor AS f (tenant_id, user_id, session_id, prefix, last_n)
    SELECT c.tenant_id, c.user_id, c.session_id, 'c', max(substring(c.name from 2)::int) FROM c
    GROUP BY c.tenant_id, c.user_id, c.session_id
    ON CONFLICT ON CONSTRAINT workspace_name_floor_pkey DO UPDATE SET last_n = greatest(f.last_n, EXCLUDED.last_n)
    RETURNING 1
  )
  SELECT 'chart'::text, c.tenant_id, NULL::text FROM c;

  RETURN QUERY
  DELETE FROM workspace_files w WHERE w.id IN (
    SELECT x.id FROM workspace_files x WHERE x.purge_at <= now() LIMIT p_limit)
  RETURNING 'file'::text, w.tenant_id, w.object_key;

  RETURN QUERY
  DELETE FROM saved_queries w WHERE w.id IN (
    SELECT x.id FROM saved_queries x WHERE x.purge_at <= now() LIMIT p_limit)
  RETURNING 'sql'::text, w.tenant_id, NULL::text;
END
$$;
REVOKE ALL ON FUNCTION odn_purge_archived_artifacts(integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION odn_purge_archived_artifacts(integer) TO odn_app;
