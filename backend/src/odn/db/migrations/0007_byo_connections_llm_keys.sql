-- User-managed data connections and bring-your-own LLM keys.
--
-- Personal connections (owner_user_id set) are visible only to their owner; tenant-shared ones
-- (owner_user_id NULL) to everyone in the tenant. User-supplied credentials are stored encrypted by the
-- API (AES-256-GCM, app master key) in secret_ciphertext; shared connections keep Secret Manager refs.

CREATE OR REPLACE FUNCTION odn_current_user() RETURNS uuid
LANGUAGE sql STABLE AS $$
  SELECT NULLIF(current_setting('odn.user_id', true), '')::uuid
$$;
GRANT EXECUTE ON FUNCTION odn_current_user() TO odn_app;

ALTER TABLE connections
  ADD COLUMN owner_user_id uuid REFERENCES users (id) ON DELETE CASCADE,
  ADD COLUMN secret_ciphertext bytea,
  ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now(),
  ADD CONSTRAINT connections_single_secret CHECK (secret_ref IS NULL OR secret_ciphertext IS NULL),
  -- A personal connection must never point at a tenant Secret Manager credential.
  ADD CONSTRAINT connections_personal_no_secret_ref CHECK (owner_user_id IS NULL OR secret_ref IS NULL);

ALTER TABLE connections DROP CONSTRAINT connections_tenant_id_name_key;
CREATE UNIQUE INDEX connections_shared_name ON connections (tenant_id, name) WHERE owner_user_id IS NULL;
CREATE UNIQUE INDEX connections_personal_name ON connections (tenant_id, owner_user_id, name)
  WHERE owner_user_id IS NOT NULL;

DROP POLICY tenant_isolation ON connections;
CREATE POLICY tenant_isolation ON connections
  USING (tenant_id = odn_current_tenant() AND (owner_user_id IS NULL OR owner_user_id = odn_current_user()))
  WITH CHECK (tenant_id = odn_current_tenant() AND (owner_user_id IS NULL OR owner_user_id = odn_current_user()));

-- One bring-your-own LLM key per user; only that user (and the API on their behalf) can read it.
CREATE TABLE user_llm_keys (
  tenant_id       uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
  user_id         uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
  provider        text NOT NULL CHECK (provider IN ('gemini', 'openai', 'anthropic', 'openai_compatible')),
  model           text NOT NULL CHECK (length(model) BETWEEN 1 AND 200),
  api_base        text CHECK (api_base IS NULL OR length(api_base) <= 500),
  key_ciphertext  bytea NOT NULL,
  key_hint        text NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, user_id)
);

ALTER TABLE user_llm_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_llm_keys FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_only ON user_llm_keys
  USING (tenant_id = odn_current_tenant() AND user_id = odn_current_user())
  WITH CHECK (tenant_id = odn_current_tenant() AND user_id = odn_current_user());

REVOKE ALL ON user_llm_keys FROM odn_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON user_llm_keys TO odn_app;

-- Usage on a user's own key is recorded but never counts against the platform (trial) budgets.
ALTER TABLE llm_usage
  ADD COLUMN key_source text NOT NULL DEFAULT 'platform' CHECK (key_source IN ('platform', 'user'));
ALTER TABLE llm_usage DROP CONSTRAINT llm_usage_pkey;
ALTER TABLE llm_usage ADD PRIMARY KEY (tenant_id, usage_date, user_id, model, key_source);
