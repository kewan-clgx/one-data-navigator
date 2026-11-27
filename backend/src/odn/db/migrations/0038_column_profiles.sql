-- Data-derived profiles are tenant-scoped even for a platform connection.
CREATE TABLE metadata_column_profiles (
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  connection_id uuid NOT NULL REFERENCES connections(id) ON DELETE CASCADE,
  connection_revision uuid NOT NULL,
  credential_fingerprint text NOT NULL CHECK (credential_fingerprint ~ '^[0-9a-f]{64}$'),
  table_path text[] NOT NULL CHECK (cardinality(table_path) = 3 AND array_position(table_path, NULL) IS NULL),
  sampled_at timestamptz NOT NULL,
  sample_rows bigint NOT NULL CHECK (sample_rows > 0),
  fields jsonb NOT NULL,
  PRIMARY KEY (tenant_id, connection_id, table_path)
);

CREATE TABLE metadata_profile_jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  connection_id uuid NOT NULL REFERENCES connections(id) ON DELETE CASCADE,
  connection_revision uuid NOT NULL,
  requested_by uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  table_path text[] NOT NULL CHECK (cardinality(table_path) = 3 AND array_position(table_path, NULL) IS NULL),
  automatic boolean NOT NULL DEFAULT false,
  status text NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'skipped', 'cancelled')),
  error_code text,
  lease_token uuid,
  lease_until timestamptz,
  attempts integer NOT NULL DEFAULT 0,
  source_attempts jsonb NOT NULL DEFAULT '[]'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz
);
CREATE UNIQUE INDEX metadata_profile_one_active
  ON metadata_profile_jobs (tenant_id, connection_id, table_path) WHERE status IN ('queued', 'running');
CREATE INDEX metadata_profile_pending ON metadata_profile_jobs (created_at) WHERE status IN ('queued', 'running');

-- This contains only accounting information, not another tenant's profile or identity.
CREATE TABLE metadata_profile_budget (
  connection_id uuid NOT NULL REFERENCES connections(id) ON DELETE CASCADE,
  utc_day date NOT NULL,
  reserved_bytes bigint NOT NULL CHECK (reserved_bytes >= 0 AND reserved_bytes <= 2000000000000),
  PRIMARY KEY (connection_id, utc_day)
);

ALTER TABLE metadata_column_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE metadata_column_profiles FORCE ROW LEVEL SECURITY;
CREATE POLICY profile_tenant ON metadata_column_profiles
  USING (tenant_id = odn_current_tenant() AND EXISTS (
    SELECT 1 FROM connections c WHERE c.id = metadata_column_profiles.connection_id
      AND c.metadata_revision = metadata_column_profiles.connection_revision))
  WITH CHECK (tenant_id = odn_current_tenant() AND EXISTS (
    SELECT 1 FROM connections c WHERE c.id = metadata_column_profiles.connection_id
      AND c.metadata_revision = metadata_column_profiles.connection_revision));
CREATE POLICY profile_owner ON metadata_column_profiles
  USING (current_user = pg_get_userbyid((SELECT relowner FROM pg_class WHERE oid = 'metadata_column_profiles'::regclass)))
  WITH CHECK (current_user = pg_get_userbyid((SELECT relowner FROM pg_class WHERE oid = 'metadata_column_profiles'::regclass)));

CREATE FUNCTION odn_clear_connection_profiles() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
  IF NEW.metadata_revision IS DISTINCT FROM OLD.metadata_revision THEN
    DELETE FROM metadata_column_profiles WHERE connection_id = NEW.id;
  END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER connection_clear_profiles AFTER UPDATE ON connections
  FOR EACH ROW EXECUTE FUNCTION odn_clear_connection_profiles();

ALTER TABLE metadata_profile_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE metadata_profile_jobs FORCE ROW LEVEL SECURITY;
CREATE POLICY profile_jobs_tenant ON metadata_profile_jobs
  USING (tenant_id = odn_current_tenant() AND EXISTS (
    SELECT 1 FROM connections c WHERE c.id = metadata_profile_jobs.connection_id))
  WITH CHECK (tenant_id = odn_current_tenant() AND EXISTS (
    SELECT 1 FROM connections c WHERE c.id = metadata_profile_jobs.connection_id));
-- The no-context worker may only claim jobs via the SECURITY DEFINER function below.
CREATE POLICY profile_jobs_owner ON metadata_profile_jobs
  USING (current_user = pg_get_userbyid((SELECT relowner FROM pg_class WHERE oid = 'metadata_profile_jobs'::regclass)))
  WITH CHECK (current_user = pg_get_userbyid((SELECT relowner FROM pg_class WHERE oid = 'metadata_profile_jobs'::regclass)));

ALTER TABLE metadata_profile_budget ENABLE ROW LEVEL SECURITY;
ALTER TABLE metadata_profile_budget FORCE ROW LEVEL SECURITY;
CREATE POLICY profile_budget_owner ON metadata_profile_budget
  USING (current_user = pg_get_userbyid((SELECT relowner FROM pg_class WHERE oid = 'metadata_profile_budget'::regclass)))
  WITH CHECK (current_user = pg_get_userbyid((SELECT relowner FROM pg_class WHERE oid = 'metadata_profile_budget'::regclass)));

REVOKE ALL ON metadata_column_profiles, metadata_profile_jobs, metadata_profile_budget FROM odn_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON metadata_column_profiles, metadata_profile_jobs TO odn_app;

CREATE FUNCTION odn_claim_profile_job()
RETURNS TABLE (id uuid, tenant_id uuid, connection_id uuid, requested_by uuid, token uuid)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
  job_id uuid;
  lease uuid := gen_random_uuid();
BEGIN
  UPDATE metadata_profile_jobs j SET status = 'failed', error_code = 'attempts_exhausted',
    finished_at = clock_timestamp(), lease_token = NULL, lease_until = NULL
  WHERE j.status = 'running' AND j.lease_until <= clock_timestamp() AND j.attempts >= 3;
  SELECT j.id INTO job_id FROM metadata_profile_jobs j
  WHERE j.attempts < 3 AND
    (j.status = 'queued' OR (j.status = 'running' AND j.lease_until <= clock_timestamp()))
  ORDER BY j.created_at, j.id FOR UPDATE SKIP LOCKED LIMIT 1;
  IF job_id IS NULL THEN RETURN; END IF;
  RETURN QUERY UPDATE metadata_profile_jobs j
    SET status = 'running', lease_token = lease, lease_until = clock_timestamp() + interval '10 minutes',
        attempts = j.attempts + 1
    WHERE j.id = job_id
    RETURNING j.id, j.tenant_id, j.connection_id, j.requested_by, lease;
END
$$;
REVOKE ALL ON FUNCTION odn_claim_profile_job() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION odn_claim_profile_job() TO odn_app;

CREATE FUNCTION odn_profile_actor_cap(job_id uuid, token uuid)
RETURNS TABLE (max_bytes_billed bigint, role text)
LANGUAGE sql SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT t.max_bytes_billed, u.role::text
  FROM metadata_profile_jobs j
  JOIN tenants t ON t.id = j.tenant_id AND t.status = 'active'
  JOIN users u ON u.id = j.requested_by AND u.tenant_id = j.tenant_id AND u.status = 'active'
  JOIN connections c ON c.id = j.connection_id AND c.metadata_revision = j.connection_revision
  WHERE j.id = job_id AND j.tenant_id = odn_current_tenant()
    AND j.requested_by = odn_current_user() AND j.status = 'running'
    AND j.lease_token = token AND j.lease_until > clock_timestamp()
$$;
REVOKE ALL ON FUNCTION odn_profile_actor_cap(uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION odn_profile_actor_cap(uuid, uuid) TO odn_app;

-- Only a live, authorized lease can increase the connection-global UTC-day reservation.
CREATE FUNCTION odn_reserve_profile_budget(job_id uuid, token uuid, amount bigint, source_job_id text)
RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
  cid uuid;
  tenant_cap bigint;
  day date := (clock_timestamp() AT TIME ZONE 'UTC')::date;
BEGIN
  IF amount IS NULL OR amount <= 0 OR amount > 50000000000
    OR source_job_id IS NULL OR source_job_id !~ '^odn_profile_[a-f0-9]{32}$' THEN RETURN false; END IF;
  SELECT j.connection_id, t.max_bytes_billed INTO cid, tenant_cap
  FROM metadata_profile_jobs j
  JOIN tenants t ON t.id = j.tenant_id AND t.status = 'active'
  JOIN users u ON u.id = j.requested_by AND u.tenant_id = j.tenant_id AND u.status = 'active'
  JOIN connections c ON c.id = j.connection_id AND c.metadata_revision = j.connection_revision
  WHERE j.id = job_id AND j.tenant_id = odn_current_tenant() AND j.requested_by = odn_current_user()
    AND j.status = 'running' AND j.lease_token = token AND j.lease_until > clock_timestamp()
  FOR UPDATE OF j;
  IF cid IS NULL OR (tenant_cap IS NOT NULL AND amount > tenant_cap) THEN RETURN false; END IF;
  INSERT INTO metadata_profile_budget (connection_id, utc_day, reserved_bytes)
  VALUES (cid, day, amount)
  ON CONFLICT (connection_id, utc_day) DO UPDATE
    SET reserved_bytes = metadata_profile_budget.reserved_bytes + EXCLUDED.reserved_bytes
    WHERE metadata_profile_budget.reserved_bytes + EXCLUDED.reserved_bytes <= 2000000000000;
  IF NOT FOUND THEN RETURN false; END IF;
  UPDATE metadata_profile_jobs
  SET source_attempts = source_attempts || jsonb_build_array(
    jsonb_build_object('job_id', source_job_id, 'utc_day', day, 'reserved_bytes', amount))
  WHERE id = job_id;
  RETURN true;
END
$$;
REVOKE ALL ON FUNCTION odn_reserve_profile_budget(uuid, uuid, bigint, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION odn_reserve_profile_budget(uuid, uuid, bigint, text) TO odn_app;
