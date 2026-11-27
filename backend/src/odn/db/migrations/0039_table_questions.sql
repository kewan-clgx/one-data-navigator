-- Table questions ("what you can ask") and their cached answers (docs/table_questions_plan.md).
--
-- Questions are tenant-scoped like other derived metadata. Visibility follows the connection: a personal connection
-- is visible only to its owner (connections RLS), so its questions are too. A question that tripped the privacy
-- filter is private to the person who asked it (private_to). Cached answers are additionally tied to the
-- connection's metadata revision: new credentials or a new allowlist hide (and the trigger deletes) answers produced
-- under the old access.

CREATE TABLE metadata_question_sets (
  tenant_id          uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  connection_id      uuid NOT NULL REFERENCES connections(id) ON DELETE CASCADE,
  table_path         text[] NOT NULL CHECK (cardinality(table_path) BETWEEN 2 AND 3 AND array_position(table_path, NULL) IS NULL),
  schema_fingerprint text NOT NULL,
  status             text NOT NULL CHECK (status IN ('generating', 'ready', 'failed', 'skipped')),
  error_code         text,
  requested_by       uuid REFERENCES users(id) ON DELETE SET NULL,
  generated_at       timestamptz,
  updated_at         timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, connection_id, table_path)
);

CREATE TABLE metadata_table_questions (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id          uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  connection_id      uuid NOT NULL REFERENCES connections(id) ON DELETE CASCADE,
  table_path         text[] NOT NULL CHECK (cardinality(table_path) BETWEEN 2 AND 3 AND array_position(table_path, NULL) IS NULL),
  source             text NOT NULL CHECK (source IN ('generated', 'asked', 'curated')),
  hidden             boolean NOT NULL DEFAULT false,
  private_to         uuid REFERENCES users(id) ON DELETE CASCADE,
  title              text NOT NULL CHECK (length(title) BETWEEN 1 AND 60),
  question           text NOT NULL CHECK (length(question) BETWEEN 1 AND 300),
  normalized_hash    text NOT NULL CHECK (normalized_hash ~ '^[0-9a-f]{64}$'),
  schema_fingerprint text,
  ask_count          integer NOT NULL DEFAULT 0,
  distinct_askers    integer NOT NULL DEFAULT 0,
  last_asked_at      timestamptz,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_by         uuid REFERENCES users(id) ON DELETE SET NULL,
  UNIQUE (tenant_id, connection_id, table_path, normalized_hash)
);
CREATE INDEX metadata_table_questions_read ON metadata_table_questions (tenant_id, connection_id, table_path);

CREATE TABLE metadata_question_askers (
  tenant_id      uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  question_id    uuid NOT NULL REFERENCES metadata_table_questions(id) ON DELETE CASCADE,
  user_id        uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  first_asked_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (question_id, user_id)
);

CREATE TABLE metadata_question_answers (
  question_id         uuid PRIMARY KEY REFERENCES metadata_table_questions(id) ON DELETE CASCADE,
  tenant_id           uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  connection_id       uuid NOT NULL REFERENCES connections(id) ON DELETE CASCADE,
  connection_revision uuid NOT NULL,
  answer_md           text NOT NULL CHECK (length(answer_md) BETWEEN 1 AND 8192),
  sql                 jsonb NOT NULL DEFAULT '[]'::jsonb,
  bytes_scanned       bigint,
  answered_at         timestamptz NOT NULL DEFAULT now(),
  answered_by         uuid REFERENCES users(id) ON DELETE SET NULL,
  data_modified_at    timestamptz,
  schema_fingerprint  text
);

ALTER TABLE metadata_question_sets ENABLE ROW LEVEL SECURITY;
ALTER TABLE metadata_question_sets FORCE ROW LEVEL SECURITY;
CREATE POLICY question_sets_tenant ON metadata_question_sets
  USING (tenant_id = odn_current_tenant() AND EXISTS (
    SELECT 1 FROM connections c WHERE c.id = metadata_question_sets.connection_id))
  WITH CHECK (tenant_id = odn_current_tenant() AND EXISTS (
    SELECT 1 FROM connections c WHERE c.id = metadata_question_sets.connection_id));

ALTER TABLE metadata_table_questions ENABLE ROW LEVEL SECURITY;
ALTER TABLE metadata_table_questions FORCE ROW LEVEL SECURITY;
CREATE POLICY table_questions_tenant ON metadata_table_questions
  USING (tenant_id = odn_current_tenant()
    AND (private_to IS NULL OR private_to = odn_current_user())
    AND EXISTS (SELECT 1 FROM connections c WHERE c.id = metadata_table_questions.connection_id))
  WITH CHECK (tenant_id = odn_current_tenant()
    AND (private_to IS NULL OR private_to = odn_current_user())
    AND EXISTS (SELECT 1 FROM connections c WHERE c.id = metadata_table_questions.connection_id));

ALTER TABLE metadata_question_askers ENABLE ROW LEVEL SECURITY;
ALTER TABLE metadata_question_askers FORCE ROW LEVEL SECURITY;
CREATE POLICY question_askers_tenant ON metadata_question_askers
  USING (tenant_id = odn_current_tenant()) WITH CHECK (tenant_id = odn_current_tenant());

ALTER TABLE metadata_question_answers ENABLE ROW LEVEL SECURITY;
ALTER TABLE metadata_question_answers FORCE ROW LEVEL SECURITY;
CREATE POLICY question_answers_tenant ON metadata_question_answers
  USING (tenant_id = odn_current_tenant() AND EXISTS (
    SELECT 1 FROM connections c WHERE c.id = metadata_question_answers.connection_id
      AND c.metadata_revision = metadata_question_answers.connection_revision))
  WITH CHECK (tenant_id = odn_current_tenant() AND EXISTS (
    SELECT 1 FROM connections c WHERE c.id = metadata_question_answers.connection_id
      AND c.metadata_revision = metadata_question_answers.connection_revision));

CREATE FUNCTION odn_clear_connection_answers() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
  IF NEW.metadata_revision IS DISTINCT FROM OLD.metadata_revision THEN
    DELETE FROM metadata_question_answers WHERE connection_id = NEW.id;
  END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER connection_clear_answers AFTER UPDATE ON connections
  FOR EACH ROW EXECUTE FUNCTION odn_clear_connection_answers();

REVOKE ALL ON metadata_question_sets, metadata_table_questions, metadata_question_askers, metadata_question_answers
  FROM odn_app;
GRANT SELECT, INSERT, UPDATE, DELETE
  ON metadata_question_sets, metadata_table_questions, metadata_question_askers, metadata_question_answers TO odn_app;
