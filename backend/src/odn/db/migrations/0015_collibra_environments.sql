-- Collibra catalog environments per tenant (docs/collibra_int_phase5_plan.md). QueryMuse calls Collibra as the
-- signed-in user with a short-lived token it signs (option A); Collibra trusts QueryMuse's JWKS. Managed by the
-- operator CLI (python -m odn.db.provision collibra ...); the app only reads it.
CREATE TABLE collibra_environments (
  tenant_id        uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
  env              text NOT NULL CHECK (env ~ '^[a-z][a-z0-9_-]{0,15}$'),
  base_url         text NOT NULL CHECK (base_url ~ '^https://[a-z0-9.-]+(:[0-9]+)?$'),
  audience         text NOT NULL CHECK (length(audience) BETWEEN 1 AND 200),
  -- Which part of the verified work email becomes the Collibra username (JWT "sub").
  principal_format text NOT NULL DEFAULT 'email' CHECK (principal_format IN ('email', 'email_local_part')),
  enabled          boolean NOT NULL DEFAULT true,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, env)
);

ALTER TABLE collibra_environments ENABLE ROW LEVEL SECURITY;
ALTER TABLE collibra_environments FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON collibra_environments
  USING (tenant_id = odn_current_tenant()) WITH CHECK (tenant_id = odn_current_tenant());

REVOKE ALL ON collibra_environments FROM odn_app;
GRANT SELECT ON collibra_environments TO odn_app;
