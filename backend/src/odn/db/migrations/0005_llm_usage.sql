-- LLM token usage per tenant/user/day (UTC) and optional per-tenant daily token limits.
CREATE TABLE llm_usage (
  tenant_id          uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
  user_id            uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
  usage_date         date NOT NULL DEFAULT (now() AT TIME ZONE 'utc')::date,
  model              text NOT NULL,
  requests           integer NOT NULL DEFAULT 0,
  prompt_tokens      bigint NOT NULL DEFAULT 0,
  completion_tokens  bigint NOT NULL DEFAULT 0,
  updated_at         timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, usage_date, user_id, model)
);

-- Overrides ODN_LLM_DAILY_TOKEN_BUDGET for one tenant; managed by operators (no app write access).
CREATE TABLE tenant_llm_budgets (
  tenant_id          uuid PRIMARY KEY REFERENCES tenants (id) ON DELETE CASCADE,
  daily_token_limit  bigint NOT NULL CHECK (daily_token_limit >= 0),
  updated_at         timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE llm_usage ENABLE ROW LEVEL SECURITY;
ALTER TABLE llm_usage FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON llm_usage
  USING (tenant_id = odn_current_tenant()) WITH CHECK (tenant_id = odn_current_tenant());

ALTER TABLE tenant_llm_budgets ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_llm_budgets FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON tenant_llm_budgets
  USING (tenant_id = odn_current_tenant()) WITH CHECK (tenant_id = odn_current_tenant());

REVOKE ALL ON llm_usage, tenant_llm_budgets FROM odn_app;
GRANT SELECT, INSERT, UPDATE ON llm_usage TO odn_app;
GRANT SELECT ON tenant_llm_budgets TO odn_app;
